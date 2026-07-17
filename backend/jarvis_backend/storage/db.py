"""SQLite connection setup. One connection, WAL mode, foreign keys enforced."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = "1"


def connect(path: Path | str) -> sqlite3.Connection:
    # check_same_thread=False: CPython's sqlite3 builds use SQLite's serialized
    # threading mode, so cross-thread use is safe. The server touches the DB
    # from the event loop; tests drive it from a TestClient portal thread.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    schema = resources.files("jarvis_backend.storage").joinpath("schema.sql").read_text("utf-8")
    with conn:
        conn.executescript(schema)
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
