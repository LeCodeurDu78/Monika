"""Secret masking for audit entries and log records."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

MASK = "***"

# Dict keys whose values are always hidden ("max_tokens" is deliberately not matched)
_SENSITIVE_KEY = re.compile(
    r"password|passwd|passphrase|secret|token(?!s\b)|api[_-]?key|authorization|"
    r"cookie|private[_-]?key|credential",
    re.IGNORECASE,
)

# Free-text patterns that look like credentials
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
]

# key=value or key: value assignments inside text (the value is replaced)
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|passphrase|secret|token|api[_-]?key)\b(\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|\S+)"
)


class Masker:
    """Replace secrets in strings and in nested structures."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        # Longest first so a secret containing another is fully replaced; short values are ignored
        self._secrets = sorted(
            {s for s in secrets if s and len(s) >= 6}, key=len, reverse=True
        )

    def mask_text(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        for pattern in _PATTERNS:
            text = pattern.sub(MASK, text)
        return _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)

    def mask(self, obj: Any) -> Any:
        """Return a masked deep copy of strings, dicts, lists and tuples."""
        if isinstance(obj, str):
            return self.mask_text(obj)
        if isinstance(obj, dict):
            return {
                k: MASK if isinstance(k, str) and _SENSITIVE_KEY.search(k) else self.mask(v)
                for k, v in obj.items()
            }
        if isinstance(obj, (list, tuple)):
            return [self.mask(v) for v in obj]
        return obj


class MaskingFilter(logging.Filter):
    """Logging filter that masks secrets in every record."""

    def __init__(self, masker: Masker) -> None:
        super().__init__()
        self._masker = masker

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._masker.mask_text(record.getMessage())
        record.args = None
        return True
