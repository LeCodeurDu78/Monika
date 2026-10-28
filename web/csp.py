"""Content-Security-Policy helpers: allow the page's own inline import map without opening script-src to 'unsafe-inline'."""

from __future__ import annotations

import base64
import hashlib
import re

# Only <script type="importmap"> blocks are allowed inline: they declare module paths and cannot run code.
_IMPORT_MAP = re.compile(r'<script\s+type="importmap"\s*>(.*?)</script>', re.DOTALL | re.IGNORECASE)


def inline_script_hashes(html: str) -> list[str]:
    """CSP hash sources ('sha256-...') for each inline import map of the page, computed on its exact text."""
    hashes = []
    for match in _IMPORT_MAP.finditer(html):
        digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
        hashes.append(f"'sha256-{base64.b64encode(digest).decode('ascii')}'")
    return hashes
