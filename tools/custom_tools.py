"""Outils perso : des serveurs MCP que Monika écrit, vérifie et teste elle-même.

Flux fixe : génération dans mcp_custom/<nom>/ -> vérification statique -> test exécuté
dans un bac à sable (réseau ouvert, disque absent) -> enregistrement dans mcp_servers.json,
sans approbation à demander. Le réseau est autorisé : un outil qui ne peut joindre aucune
API ne sert à rien. L'empreinte du code est gardée : un serveur dont le code a changé depuis sa
vérification est désactivé, et il faut le régénérer (il sera revérifié et retesté).

Ces règles sont des constantes du module et create_custom_tools n'écrit que dans
mcp_custom/<nom>/ : il ne peut modifier ni son propre code ni ces garde-fous.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from monika.paths import Paths
from tools.registry import Tool, ToolFailure, ToolRegistry

MAX_CHARS = 60_000
TEST_TIMEOUT_S = 60.0
DOCKER_TIMEOUT_S = 240.0  # first run pulls the image and installs mcp
DOCKER_IMAGE = "python:3.12-slim"

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
_LABELS = {
    "active": "actif",
    "disabled": "désactivé",
    "refused": "refusé",
}

# Évasion du bac à sable et exécution arbitraire. Le réseau, lui, n'est plus interdit :
# un outil perso doit pouvoir appeler une API (requests, httpx, urllib, socket…).
_FORBIDDEN_IMPORTS = frozenset(
    {
        "cffi",
        "ctypes",
        "importlib",
        "marshal",
        "multiprocessing",
        "pickle",
        "pty",
        "shelve",
        "shutil",
        "subprocess",
    }
)
_FORBIDDEN_CALLS = frozenset({"eval", "exec", "compile", "__import__", "breakpoint"})
_FORBIDDEN_OS = frozenset(
    {
        "execl",
        "execle",
        "execlp",
        "execv",
        "execve",
        "execvp",
        "fork",
        "forkpty",
        "kill",
        "killpg",
        "popen",
        "spawnl",
        "spawnv",
        "spawnve",
        "system",
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fingerprint(folder: Path) -> str:
    """SHA-256 du code du serveur et de son test : ce que la vérification a couvert."""
    digest = hashlib.sha256()
    for name in ("server.py", "test_server.py"):
        digest.update(name.encode())
        digest.update((folder / name).read_bytes())
    return digest.hexdigest()


def _safe_fingerprint(folder: Path) -> str:
    try:
        return _fingerprint(folder)
    except OSError:
        return ""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "(illisible)"


def _write_text(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic private write: a half-written server list must never reach the next launch."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def scan_source(label: str, source: str) -> tuple[list[str], set[str]]:
    """Refus et modules importés d'un fichier, sans l'exécuter."""
    problems: list[str] = []
    modules: set[str] = set()
    try:
        tree = ast.parse(source, filename=label)
    except SyntaxError as exc:
        return [f"{label} : erreur de syntaxe ligne {exc.lineno} ({exc.msg})"], modules
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALLS:
                problems.append(f"{label} : {func.id}() est interdit")
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr in _FORBIDDEN_OS
            ):
                problems.append(f"{label} : os.{func.attr}() est interdit")
    return problems, modules


def static_check(code: str, test: str) -> tuple[list[str], list[str]]:
    """Raisons de refuser le serveur (vide = accepté) et modules qu'il utilise."""
    problems: list[str] = []
    server_modules: set[str] = set()
    all_modules: set[str] = set()
    for label, source in (("server.py", code), ("test_server.py", test)):
        found, modules = scan_source(label, source)
        problems += found
        all_modules |= modules
        if label == "server.py":
            server_modules = modules
    problems += [
        f"module interdit : {module}"
        for module in sorted(all_modules & _FORBIDDEN_IMPORTS)
    ]
    if "mcp" not in server_modules:
        problems.append(
            "server.py doit exposer ses outils avec le paquet mcp (import mcp)"
        )
    return problems, sorted(all_modules)


def sandbox_argv(folder: Path) -> list[str]:
    """bwrap: réseau ouvert, seul le dossier du serveur est inscriptible, /usr en lecture."""
    exe = sys.executable
    argv = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--share-net",  # le test doit pouvoir joindre les mêmes API que l'outil
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib",
        "/lib64",
        "--ro-bind-try",
        "/etc/ld.so.cache",
        "/etc/ld.so.cache",
        # DNS et autorités de certification : sans eux, un outil « réseau » ne résout rien
        "--ro-bind-try",
        "/etc/resolv.conf",
        "/etc/resolv.conf",
        "--ro-bind-try",
        "/etc/hosts",
        "/etc/hosts",
        "--ro-bind-try",
        "/etc/nsswitch.conf",
        "/etc/nsswitch.conf",
        "--ro-bind-try",
        "/etc/services",
        "/etc/services",
        "--ro-bind-try",
        "/etc/protocols",
        "/etc/protocols",
        "--ro-bind-try",
        "/etc/ssl",
        "/etc/ssl",
        "--ro-bind-try",
        "/etc/ca-certificates",
        "/etc/ca-certificates",
        "--ro-bind-try",
        "/etc/pki",
        "/etc/pki",
        "--bind",
        str(folder),
        str(folder),
        "--chdir",
        str(folder),
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        str(folder),
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--setenv",
        "PYTHONNOUSERSITE",
        "1",
        "--setenv",
        "PYTHONIOENCODING",
        "utf-8",
    ]
    prefix = Path(sys.prefix)
    if prefix != Path(sys.base_prefix):  # The venv that holds mcp: read-only, nothing else of $HOME
        argv += ["--ro-bind", str(prefix), str(prefix)]
    return [*argv, exe, "test_server.py"]


def docker_argv(folder: Path) -> list[str]:
    """Docker (Windows) : même garantie que bwrap — seul le dossier de l'outil est monté, réseau ouvert."""
    tag = hashlib.sha256(str(folder).encode()).hexdigest()[:12]
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        f"monika-outil-{tag}",
        "--network",
        "bridge",
        "-v",
        f"{folder}:/outil",
        "-w",
        "/outil",
        "-v",
        "monika-pip:/root/.cache/pip",  # mcp et les paquets restent en cache entre deux tests
        "-e",
        "PYTHONIOENCODING=utf-8",
        DOCKER_IMAGE,
        "sh",
        "-lc",
        "pip install --quiet --disable-pip-version-check mcp requests httpx "
        "&& python test_server.py",
    ]


def sandbox_command(folder: Path) -> tuple[list[str], float]:
    """Le bac à sable disponible ici, et combien de temps son test peut durer."""
    if shutil.which("bwrap"):
        return sandbox_argv(folder), TEST_TIMEOUT_S
    if shutil.which("docker"):
        return docker_argv(folder), DOCKER_TIMEOUT_S
    raise ToolFailure(
        "Aucun bac à sable disponible : sans lui, je ne crée pas d'outil perso. Sous Linux, "
        "installe bubblewrap (sudo pacman -S bubblewrap) ; sous Windows, installe Docker "
        "Desktop, puis réessaie."
    )


async def run_sandboxed(folder: Path) -> tuple[bool, str]:
    """Exécute test_server.py dans le bac à sable ; (réussi, sortie)."""
    argv, timeout_s = sandbox_command(folder)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        raise ToolFailure(f"Bac à sable impossible à lancer : {exc}") from exc
    try:
        async with asyncio.timeout(timeout_s):
            out, _ = await proc.communicate()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False, f"délai dépassé ({timeout_s:g} s)"
    text = out.decode("utf-8", "replace").strip()
    return proc.returncode == 0, (text[-4000:] if text else "(aucune sortie)")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


class CustomTools:
    """Le dossier mcp_custom/, son index (empreintes) et le fichier des serveurs MCP."""

    def __init__(self, paths: Paths) -> None:
        self._root = paths.custom_tools
        self._config = paths.mcp_config
        self._index = paths.custom_tools_index

    # --- stockage ---------------------------------------------------------------
    def _servers(self) -> dict[str, dict[str, Any]]:
        servers = _read_json(self._index).get("serveurs")
        return dict(servers) if isinstance(servers, dict) else {}

    def _save(self, servers: dict[str, dict[str, Any]]) -> None:
        _write_json(self._index, {"serveurs": servers})

    def _config_servers(self) -> dict[str, Any]:
        """Le contenu de mcp_servers.json ; un fichier illisible n'est jamais écrasé."""
        if self._config.exists():
            try:
                json.loads(self._config.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ToolFailure(
                    f"{self._config} est illisible ({exc}) : je ne le modifie pas. "
                    "Corrige-le d'abord."
                ) from exc
        data = _read_json(self._config)
        servers = data.get("mcpServers")
        return dict(servers) if isinstance(servers, dict) else {}

    def _save_config(self, servers: dict[str, Any]) -> None:
        data = _read_json(self._config)
        _write_json(self._config, {**data, "mcpServers": servers})

    def _register(self, name: str, folder: Path) -> None:
        entry = {
            "command": sys.executable,
            "args": ["server.py"],
            "cwd": str(folder),
            "enabled": True,
        }
        servers = self._config_servers()
        existing = servers.get(name)
        if isinstance(existing, dict) and existing.get("cwd") != str(folder):
            raise ToolFailure(
                f"Un serveur MCP nommé « {name} » existe déjà dans {self._config} et "
                "n'a pas été créé ici : choisis un autre nom (je ne touche pas aux "
                "serveurs que tu as écrits à la main)."
            )
        servers[name] = entry
        self._save_config(servers)

    def _unregister(self, name: str, folder: Path) -> bool:
        """Désactive l'entrée de notre serveur ; False si elle n'existe pas ou n'est pas à nous."""
        try:
            servers = self._config_servers()
        except ToolFailure:  # Désactivation au mieux : ne bloque jamais un lancement
            return False
        entry = servers.get(name)
        if not isinstance(entry, dict) or entry.get("cwd") != str(folder):
            return False
        if entry.get("enabled") is False:
            return False
        servers[name] = {**entry, "enabled": False}
        self._save_config(servers)
        return True

    def _remove(self, name: str, folder: Path) -> bool:
        servers = self._config_servers()
        entry = servers.get(name)
        if not isinstance(entry, dict) or entry.get("cwd") != str(folder):
            return False
        servers.pop(name)
        self._save_config(servers)
        return True

    # --- génération -------------------------------------------------------------
    async def prepare(
        self, name: str, description: str, code: str, test: str
    ) -> str:
        """Écrit, vérifie, teste et enregistre un serveur ; rien n'est demandé à l'utilisateur."""
        name = (name or "").strip().lower()
        if not _NAME.match(name):
            raise ToolFailure(
                "« nom » doit faire 2 à 32 caractères : minuscules, chiffres, - ou _ "
                "(il devient le dossier mcp_custom/<nom> et le nom du serveur)."
            )
        if not code.strip() or not test.strip():
            raise ToolFailure(
                "Donne le code du serveur (code) ET son test (test) : les deux sont "
                "nécessaires pour vérifier le serveur."
            )
        for label, text in (("code", code), ("test", test)):
            if len(text) > MAX_CHARS:
                raise ToolFailure(
                    f"« {label} » dépasse {MAX_CHARS} caractères : découpe le serveur en "
                    "outils plus simples."
                )
        folder = self._root / name
        existing = self._config_servers().get(name)
        if isinstance(existing, dict) and existing.get("cwd") != str(folder):
            raise ToolFailure(
                f"Un serveur MCP nommé « {name} » existe déjà dans {self._config} et "
                "n'a pas été créé ici : choisis un autre nom (je ne touche pas aux "
                "serveurs que tu as écrits à la main)."
            )
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        _write_text(folder / "server.py", code)
        _write_text(folder / "test_server.py", test)

        servers = self._servers()
        meta = servers.get(name, {})
        meta.update(
            {
                "empreinte": _fingerprint(folder),
                "description": " ".join((description or "").split())[:300],
                "updated": _now(),
            }
        )

        problems, modules = static_check(code, test)
        if problems:
            meta["status"] = "refused"
            servers[name] = meta
            self._save(servers)
            self._unregister(name, folder)
            raise ToolFailure(
                f"Serveur « {name} » refusé par la vérification statique : rien n'est "
                "enregistré.\n"
                + "\n".join(f"  - {problem}" for problem in problems)
                + f"\nLe code est dans {folder} : /outils-perso code {name} pour le relire."
            )

        ok, output = await run_sandboxed(folder)
        if not ok:
            meta["status"] = "refused"
            servers[name] = meta
            self._save(servers)
            self._unregister(name, folder)
            raise ToolFailure(
                f"Les tests de « {name} » ont échoué dans le bac à sable : rien n'est "
                f"enregistré.\n{output}\n"
                "Corrige le code ou le test, puis rappelle create_custom_tools."
            )

        self._register(name, folder)
        meta["status"] = "active"
        servers[name] = meta
        self._save(servers)
        return self._report(name, folder, meta, modules, output)

    def _report(
        self,
        name: str,
        folder: Path,
        meta: dict[str, Any],
        modules: list[str],
        output: str,
    ) -> str:
        return (
            f"Serveur « {name} » écrit, vérifié et testé : il est enregistré.\n"
            f"Dossier : {folder}\n"
            f"Empreinte : {str(meta['empreinte'])[:12]}\n"
            "Permissions demandées :\n"
            f"  - modules : {', '.join(modules) or 'aucun'}\n"
            "  - réseau : autorisé (je peux appeler des API et lire des pages)\n"
            f"  - fichiers : {folder} pour mon travail ; pendant le test, le reste du "
            "disque est absent\n"
            "  - sous-processus : refusés\n"
            "Vérification statique : OK\n"
            f"Tests (bac à sable, réseau ouvert) : OK\n{output}\n\n"
            f"Code du serveur ({folder / 'server.py'}) :\n"
            f"{_read_text(folder / 'server.py')}\n"
            f"Tests ({folder / 'test_server.py'}) :\n"
            f"{_read_text(folder / 'test_server.py')}\n"
            "\nEnregistré dans mcp_servers.json : ces outils seront disponibles à mon "
            "prochain démarrage (redémarre-moi).\n"
            f"Gestion : /outils-perso · /outils-perso code {name} · desactiver {name} · "
            f"supprimer {name}"
        )

    # --- consultation -----------------------------------------------------------
    def entries(self) -> list[dict[str, Any]]:
        """Un résumé par serveur : le panneau de la page et /outils-perso lisent la même chose."""
        items: list[dict[str, Any]] = []
        servers = self._servers()
        for name in sorted(servers):
            meta = servers[name]
            status = str(meta.get("status"))
            items.append(
                {
                    "name": name,
                    "state": _LABELS.get(status, status),
                    "fingerprint": str(meta.get("empreinte") or "")[:12],
                    "description": meta.get("description") or "",
                    "changed": status == "active"
                    and _safe_fingerprint(self._root / name) != meta.get("empreinte"),
                }
            )
        return items

    def status_text(self) -> str:
        items = self.entries()
        if not items:
            return (
                "Aucun outil perso pour l'instant. Demande-moi une action que mes outils "
                "ne savent pas faire : je crée le serveur MCP, je le vérifie et je le "
                "teste dans un bac à sable avant de l'enregistrer."
            )
        lines = [f"Outils perso ({len(items)}) :"]
        for item in items:
            note = " · code modifié, à revérifier" if item["changed"] else ""
            lines.append(
                f"  {item['name']} — {item['state']} · {item['fingerprint']}{note} — "
                f"{item['description']}"
            )
        lines.append("  /outils-perso code NOM · desactiver NOM · supprimer NOM")
        return "\n".join(lines)

    def show(self, name: str) -> str:
        meta = self._servers().get(name)
        if meta is None or not _NAME.match(name):
            known = ", ".join(sorted(self._servers())) or "aucun"
            raise ToolFailure(f"Aucun outil perso « {name} » (connus : {known}).")
        folder = self._root / name
        label = _LABELS.get(str(meta.get("status")), str(meta.get("status")))
        return (
            f"Serveur « {name} » — {label} · empreinte "
            f"{str(meta.get('empreinte') or '')[:12]} · {meta.get('description') or ''}\n"
            f"Dossier : {folder} · réseau autorisé · travail dans ce dossier\n\n"
            f"server.py :\n{_read_text(folder / 'server.py')}\n"
            f"test_server.py :\n{_read_text(folder / 'test_server.py')}"
        )

    # --- gestion ----------------------------------------------------------------
    def disable(self, name: str) -> str:
        servers = self._servers()
        meta = servers.get(name)
        if meta is None or not _NAME.match(name):
            raise ToolFailure(f"Aucun outil perso « {name} ».")
        meta["status"] = "disabled"
        servers[name] = meta
        self._save(servers)
        self._unregister(name, self._root / name)
        return (
            f"« {name} » désactivé : il ne démarrera plus. Son code reste dans "
            f"{self._root / name}."
        )

    def delete(self, name: str) -> str:
        servers = self._servers()
        if name not in servers or not _NAME.match(name):
            raise ToolFailure(f"Aucun outil perso « {name} ».")
        folder = self._root / name
        self._remove(name, folder)
        shutil.rmtree(folder, ignore_errors=True)
        servers.pop(name, None)
        self._save(servers)
        return f"« {name} » supprimé : son code et son entrée dans {self._config} sont partis."

    def enforce(self) -> list[str]:
        """Au démarrage : jamais de serveur dont le code a changé depuis la vérification."""
        servers = self._servers()
        notices: list[str] = []
        changed = False
        for name, meta in servers.items():
            if not _NAME.match(name):
                continue
            folder = self._root / name
            status = meta.get("status")
            if status == "active":
                if _safe_fingerprint(folder) != meta.get("empreinte"):
                    meta["status"] = "disabled"
                    self._unregister(name, folder)
                    changed = True
                    notices.append(
                        f"Outil perso « {name} » : le code a changé depuis sa "
                        "vérification, je l'ai désactivé. Demande-moi de le régénérer, il "
                        f"sera revérifié et retesté (/outils-perso code {name})."
                    )
            elif status in ("pending", "refused", "disabled") and self._unregister(
                name, folder
            ):
                changed = True
        if changed:
            self._save(servers)
        # Un serveur de mcp_custom/ absent de l'index n'est jamais lancé non plus.
        try:
            configured = self._config_servers()
        except ToolFailure:
            return notices  # Fichier illisible : rien à écrire, et le démarrage continue
        for name, entry in configured.items():
            if name in servers or not isinstance(entry, dict):
                continue
            cwd = entry.get("cwd")
            if isinstance(cwd, str) and _is_under(Path(cwd), self._root):
                self._remove(name, Path(cwd))
                notices.append(
                    f"Serveur perso « {name} » retiré de la configuration : il n'est pas "
                    "dans l'index des outils vérifiés."
                )
        return notices


def register_custom_tool(registry: ToolRegistry, custom: CustomTools) -> None:
    """Expose create_custom_tools : il écrit, vérifie, teste et enregistre."""

    async def handler(args: dict[str, Any]) -> str:
        return await custom.prepare(
            name=str(args.get("nom", "")),
            description=str(args.get("description", "")),
            code=str(args.get("code", "")),
            test=str(args.get("test", "")),
        )

    registry.register(
        Tool(
            name="create_custom_tools",
            description=(
                "Crée un nouveau serveur MCP personnel (mcp_custom/<nom>/) pour une action "
                "que tes outils ne savent pas faire. Tu écris server.py (outils exposés "
                "avec le paquet mcp : from mcp.server.mcpserver import MCPServer, transport "
                "stdio) et test_server.py (test exécutable seul : il importe server.py et "
                "vérifie ses fonctions, sans client MCP). Le serveur est vérifié puis testé "
                "dans un bac à sable avec accès réseau et accès à son seul dossier (donc "
                "requests, httpx ou urllib sont permis : l'outil pourra joindre de vraies "
                "API) ; s'il passe, il est enregistré dans mcp_servers.json et ses outils "
                "seront disponibles à mon prochain démarrage. Montre ensuite à l'utilisateur "
                "le code, les permissions "
                "demandées et le résultat des tests : il doit savoir ce que tu as ajouté. "
                "Ne touche jamais aux règles de sécurité de cet outil."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "nom": {
                        "type": "string",
                        "description": "Nom du serveur : minuscules, chiffres, - ou _ (2 à 32)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Ce que fait le serveur, en une phrase",
                    },
                    "code": {
                        "type": "string",
                        "description": "Le fichier server.py complet (outils MCP stdio)",
                    },
                    "test": {
                        "type": "string",
                        "description": "Le fichier test_server.py complet, exécutable seul",
                    },
                },
                "required": ["nom", "description", "code", "test"],
            },
            handler=handler,
            slow=True,
            filler_variants=[
                "J'écris le serveur, puis je le teste à l'abri.",
                "Je fabrique l'outil et je le passe au bac à sable.",
            ],
            source="outils perso",
        )
    )
