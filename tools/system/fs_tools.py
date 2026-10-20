"""File tools: read, write, edit, search (grep) and find (glob).

Relative paths are resolved from the user's home folder (like run_script), and `~` is expanded.
"""

import ast
import fnmatch
import glob as _glob
import os
import re
from pathlib import Path

_READ_DEFAULT_LINES = 2000
_READ_MAX_CHARS = 25000
_LINE_MAX_CHARS = 2000
_GREP_MAX_FILE_BYTES = 2_000_000
_GREP_MAX_MATCHES = 100
_GLOB_MAX_RESULTS = 200
_IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".idea"}


def _resolve(path: str) -> Path:
    p = Path(os.path.expanduser(path or "."))
    return p if p.is_absolute() else Path.home() / p


def _is_binary(sample: bytes) -> bool:
    return b"\x00" in sample


def _is_ignored(path: Path) -> bool:
    return any(part in _IGNORED_DIRS for part in path.parts)


# --- read_file ---------------------------------------------------------------------------------
def read_file(path: str, offset: int = 1, limit: int = _READ_DEFAULT_LINES) -> str:
    """Read a text file and return its numbered lines."""
    try:
        p = _resolve(path)
        if not p.exists():
            return f"❌ Fichier introuvable : {p}"
        if p.is_dir():
            entries = sorted(os.listdir(p))
            return f"'{p}' est un dossier. Contenu : " + ", ".join(entries[:200])
        with open(p, "rb") as fh:
            if _is_binary(fh.read(8192)):
                return f"❌ '{p}' semble être un fichier binaire ({p.stat().st_size} octets), lecture impossible."

        offset = max(1, int(offset or 1))
        limit = max(1, int(limit or _READ_DEFAULT_LINES))
        out, chars, total, last_shown = [], 0, 0, 0
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, start=1):
                total = lineno
                if lineno < offset:
                    continue
                if lineno >= offset + limit or chars >= _READ_MAX_CHARS:
                    continue  # we only keep going to count the total number of lines
                line = line.rstrip("\n").rstrip("\r")
                if len(line) > _LINE_MAX_CHARS:
                    line = line[:_LINE_MAX_CHARS] + f"… [ligne tronquée, {len(line)} caractères]"
                entry = f"{lineno:>6}\t{line}"
                out.append(entry)
                chars += len(entry) + 1
                last_shown = lineno

        if total == 0:
            return f"Fichier '{p}' vide."
        if not out:
            return f"❌ offset {offset} au-delà de la fin du fichier ({total} lignes)."
        header = f"{p} — lignes {offset}-{last_shown} sur {total}"
        footer = ""
        if last_shown < total:
            footer = f"\n[... {total - last_shown} lignes restantes : relance read_file avec offset={last_shown + 1}]"
        return header + "\n" + "\n".join(out) + footer
    except Exception as exc:
        return f"❌ Erreur de lecture : {type(exc).__name__}: {exc}"


# --- write_file --------------------------------------------------------------------------------
def write_file(path: str, content: str) -> str:
    """Create a file (or overwrite the existing one) with the given content."""
    try:
        p = _resolve(path)
        if p.is_dir():
            return f"❌ '{p}' est un dossier."
        existed = p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        text = content if isinstance(content, str) else str(content)
        p.write_text(text, encoding="utf-8")
        lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        verb = "écrasé" if existed else "créé"
        return f"✅ Fichier {verb} : {p} ({lines} lignes, {len(text.encode('utf-8'))} octets)." + _syntax_warning(p, text)
    except Exception as exc:
        return f"❌ Erreur d'écriture : {type(exc).__name__}: {exc}"


# --- edit_file ---------------------------------------------------------------------------------
def _syntax_warning(p: Path, text: str) -> str:
    if p.suffix != ".py":
        return ""
    try:
        ast.parse(text)
        return ""
    except SyntaxError as exc:
        return f"\n⚠️ Le fichier Python n'est plus syntaxiquement valide (ligne {exc.lineno}: {exc.msg}). Corrige-le."


def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Replace an exact string in an existing file."""
    try:
        p = _resolve(path)
        if not p.is_file():
            return f"❌ Fichier introuvable : {p}"
        if old_string == new_string:
            return "❌ old_string et new_string sont identiques : rien à modifier."
        if not old_string:
            return "❌ old_string est vide. Pour créer ou réécrire un fichier, utilise write_file."

        raw = p.read_bytes()
        if _is_binary(raw[:8192]):
            return f"❌ '{p}' est un fichier binaire."
        text = raw.decode("utf-8", errors="replace")

        # CRLF line endings: the model writes \n, so adapt for the match to work.
        crlf = "\r\n" in text
        if crlf:
            text = text.replace("\r\n", "\n")

        count = text.count(old_string)
        if count == 0:
            hint = ""
            stripped = old_string.strip()
            if stripped and stripped in text:
                hint = " Le texte existe mais avec une indentation ou des espaces différents : copie-le exactement depuis read_file."
            return f"❌ old_string introuvable dans {p}.{hint} Relis le fichier avec read_file et recopie le passage exact."
        if count > 1 and not replace_all:
            return (
                f"❌ old_string apparaît {count} fois dans {p}. Ajoute du contexte autour (lignes voisines) "
                "pour le rendre unique, ou passe replace_all=true pour tout remplacer."
            )

        first = text.index(old_string)
        line_no = text.count("\n", 0, first) + 1
        new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        if crlf:
            new_text = new_text.replace("\n", "\r\n")
        p.write_bytes(new_text.encode("utf-8"))
        replaced = count if replace_all else 1
        return f"✅ {p} modifié : {replaced} remplacement(s), à partir de la ligne {line_no}." + _syntax_warning(
            p, new_text.replace("\r\n", "\n")
        )
    except Exception as exc:
        return f"❌ Erreur d'édition : {type(exc).__name__}: {exc}"


# --- grep --------------------------------------------------------------------------------------
def _iter_files(root: Path, include: str | None):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
        for name in filenames:
            if include and not fnmatch.fnmatch(name, include):
                continue
            yield Path(dirpath) / name


def grep(
    pattern: str,
    path: str = ".",
    include: str = "",
    ignore_case: bool = False,
    context: int = 0,
    max_results: int = _GREP_MAX_MATCHES,
) -> str:
    """Search a regular expression in the text files of a folder (recursively)."""
    try:
        root = _resolve(path)
        if not root.exists():
            return f"❌ Chemin introuvable : {root}"
        try:
            rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            return f"❌ Expression régulière invalide : {exc}"

        context = max(0, min(int(context or 0), 10))
        max_results = max(1, min(int(max_results or _GREP_MAX_MATCHES), 500))
        results, matches, files_hit = [], 0, 0
        for fp in _iter_files(root, include or None):
            try:
                if fp.stat().st_size > _GREP_MAX_FILE_BYTES:
                    continue
                with open(fp, "rb") as fh:
                    if _is_binary(fh.read(4096)):
                        continue
                lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            hit_lines = [i for i, line in enumerate(lines) if rx.search(line)]
            if not hit_lines:
                continue
            files_hit += 1
            shown = set()
            for i in hit_lines:
                if matches >= max_results:
                    break
                matches += 1
                for j in range(max(0, i - context), min(len(lines), i + context + 1)):
                    if j in shown:
                        continue
                    shown.add(j)
                    sep = ":" if j == i else "-"
                    results.append(f"{fp}{sep}{j + 1}{sep} {lines[j][:300]}")
                if context:
                    results.append("--")
            if matches >= max_results:
                break

        if not results:
            return f"Aucune correspondance pour /{pattern}/ dans {root}."
        note = f"\n[Limite de {max_results} correspondances atteinte : affine le motif ou le dossier.]" if matches >= max_results else ""
        return f"{matches} correspondance(s) dans {files_hit} fichier(s) :\n" + "\n".join(results) + note
    except Exception as exc:
        return f"❌ Erreur grep : {type(exc).__name__}: {exc}"


# --- glob --------------------------------------------------------------------------------------
def glob(pattern: str, path: str = ".") -> str:
    """Find files by pattern (e.g. '**/*.py'), most recently modified first."""
    try:
        root = _resolve(path)
        if not root.is_dir():
            return f"❌ Dossier introuvable : {root}"
        found = []
        for match in _glob.iglob(pattern, root_dir=str(root), recursive=True):
            full = root / match
            if _is_ignored(Path(match)):
                continue
            try:
                found.append((full.stat().st_mtime, full, full.is_dir()))
            except OSError:
                continue
            if len(found) >= 5000:
                break
        if not found:
            return f"Aucun fichier ne correspond à '{pattern}' dans {root}."
        found.sort(key=lambda item: item[0], reverse=True)
        lines = [f"{item[1]}{'/' if item[2] else ''}" for item in found[:_GLOB_MAX_RESULTS]]
        extra = f"\n[... {len(found) - _GLOB_MAX_RESULTS} autres résultats, affine le motif]" if len(found) > _GLOB_MAX_RESULTS else ""
        return f"{len(found)} résultat(s) pour '{pattern}' dans {root} (récents d'abord) :\n" + "\n".join(lines) + extra
    except Exception as exc:
        return f"❌ Erreur glob : {type(exc).__name__}: {exc}"
