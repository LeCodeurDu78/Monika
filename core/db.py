"""Utilitaires SQLite."""

import os
import sqlite3
from pathlib import Path
from typing import Union

from core.settings import settings


def db_path(filename: str) -> str:
    """Path of a SQLite database."""
    return str(settings.APP_DIR / "databases" / filename)


def get_connection(path: Union[str, Path]) -> sqlite3.Connection:
    """Open a SQLite connection."""
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    return sqlite3.connect(str(path))


def init_table(path: Union[str, Path], create_sql: str) -> None:
    """Run a schema-creation script."""
    with get_connection(path) as conn:
        conn.executescript(create_sql)
        conn.commit()