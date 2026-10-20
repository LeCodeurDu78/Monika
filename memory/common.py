"""Pieces shared by Monika's memory backends."""


class MemoryUnavailable(RuntimeError):
    """A memory backend is not usable (missing dependency, server down, too slow...)."""


def log(message: str) -> None:
    """Print a memory message to the terminal (same style as the rest of Monika)."""
    print(message, flush=True)


def truncate(text: str, limit: int) -> str:
    """Cut `text` to at most `limit` characters, appending "…" if something was removed."""
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def describe_error(exc: BaseException) -> str:
    """Readable error message, with the way forward when the cause is a missing module."""
    if isinstance(exc, ModuleNotFoundError):
        return f"module Python manquant « {exc.name} »"
    return f"{type(exc).__name__}: {exc}"
