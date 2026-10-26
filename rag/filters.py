"""What never gets indexed: hidden files, caches, and anything that looks like a secret."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

# Always skipped, even when hidden files are included
SECRET_FILE_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.kdbx", "*.kdb", "*.gpg", "*.pgp",
    ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials", ".pgpass", ".htpasswd",
    "credentials", "credentials.*", "secrets.*", "secret.*", "*.secret", "*.secrets",
    "passwords.*", "password.*", "*.ovpn", "wallet.dat", "*.tfstate", "*.tfvars",
    "service-account*.json", "*.sqlite-wal",
)
SECRET_DIRS = frozenset({
    ".ssh", ".gnupg", ".aws", ".kube", ".docker", ".password-store", ".terraform", ".azure",
})
# Heavy or generated folders that only add noise
SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", "venv", ".venv", "site-packages", "dist", "build", "target",
    "bower_components", ".git", ".hg", ".svn", ".cache", ".tox", ".mypy_cache", ".pytest_cache",
    ".gradle", ".m2", ".cargo", ".rustup", "snap", ".Trash", "Trash", "lost+found",
})

# Content that means "this file holds a credential": the whole file is skipped
SECRET_CONTENT: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("clé privée", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("clé AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("jeton GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("clé API (sk-)", re.compile(r"\bsk-[A-Za-z0-9_-]{32,}")),
    ("jeton Slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("clé Google", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
)


def find_secret(text: str) -> str | None:
    """Return a short reason if the text contains a credential, else None."""
    for reason, pattern in SECRET_CONTENT:
        if pattern.search(text):
            return reason
    return None


class PathFilter:
    """Decides whether a directory or file is eligible for indexing."""

    def __init__(self, extra_patterns: list[str] | None = None, include_hidden: bool = False,
                 forbidden: list[Path] | None = None) -> None:
        self._extra = extra_patterns or []
        self._hidden = include_hidden
        self._forbidden = [p.resolve() for p in (forbidden or [])]

    def _matches_extra(self, path: Path, name: str) -> bool:
        return any(fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(str(path), pat)
                   for pat in self._extra)

    def dir_reason(self, path: Path) -> str | None:
        """Why a directory is skipped (None means: descend into it)."""
        name = path.name
        if name in SECRET_DIRS:
            return "dossier de secrets"
        if name in SKIP_DIRS:
            return "dossier exclu"
        if name.startswith(".") and not self._hidden:
            return "dossier caché"
        if any(path == f or f in path.parents for f in self._forbidden):
            return "données de Monika"
        if self._matches_extra(path, name):
            return "motif RAG_EXCLUDE"
        return None

    def file_reason(self, path: Path) -> str | None:
        """Why a file is skipped by name (None means: eligible)."""
        name = path.name
        if any(fnmatch.fnmatch(name.lower(), pat) for pat in SECRET_FILE_PATTERNS):
            return "fichier de secrets"
        if name.startswith(".") and not self._hidden:
            return "fichier caché"
        if self._matches_extra(path, name):
            return "motif RAG_EXCLUDE"
        return None
