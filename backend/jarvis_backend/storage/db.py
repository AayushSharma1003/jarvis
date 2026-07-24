"""SQLite connection setup. One connection, WAL mode, foreign keys enforced."""

from __future__ import annotations

import logging
import sqlite3
import time
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the store, recovering from a corrupt file rather than crashing.

    A corrupt or foreign database (junk bytes, a truncated write, some other
    program's file landing at our path) raises `sqlite3.DatabaseError` on the
    first read of the header — which used to propagate out of `main.py` and
    strand sidecar startup behind an opaque "backend didn't start in time".
    Instead we move the bad file aside and start fresh; the old file is kept,
    not deleted, so a user can still try to recover data from it.
    """
    try:
        return _open(path)
    except sqlite3.DatabaseError as e:
        # Recovery only makes sense for a real on-disk file. `:memory:`, a URI,
        # or a path that isn't a file has nothing to rename, so a DatabaseError
        # from one of those is a genuine fault and must surface.
        p = Path(path)
        if not p.is_file():
            raise
        # Timestamped sibling so repeated bad starts don't clobber each other,
        # and so the original name is free for the fresh database.
        backup = p.with_name(f"{p.name}.corrupt-{int(time.time())}")
        # A failed rename is a filesystem problem (full disk, permissions), not
        # a corruption one — let it propagate rather than pretending we recovered.
        p.rename(backup)
        logger.warning(
            "database at %s could not be opened (%s); moved it to %s and started "
            "a fresh database. The old file is kept for manual recovery.",
            p,
            e,
            backup,
        )
        return _open(path)


def _open(path: Path | str) -> sqlite3.Connection:
    # check_same_thread=False: CPython's sqlite3 builds use SQLite's serialized
    # threading mode, so cross-thread use is safe. The server touches the DB
    # from the event loop; tests drive it from a TestClient portal thread.
    conn = sqlite3.connect(path, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = NORMAL")
        _init_schema(conn)
    except sqlite3.DatabaseError:
        # Release the handle before the caller renames the file: an open handle
        # blocks the rename on Windows, and closing is correct everywhere else.
        conn.close()
        raise
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    schema = resources.files("jarvis_backend.storage").joinpath("schema.sql").read_text("utf-8")
    with conn:
        conn.executescript(schema)
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
