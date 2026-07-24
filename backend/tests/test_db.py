"""Opening the store — including when the file on disk is not a database."""

from __future__ import annotations

import logging
import sqlite3

import pytest

from jarvis_backend.storage import db


def test_connect_opens_a_fresh_database(tmp_path):
    conn = db.connect(tmp_path / "jarvis.sqlite3")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_a_corrupt_database_is_renamed_aside_and_a_fresh_one_created(tmp_path, caplog):
    """A junk file at the db path must not crash startup.

    Before this, `sqlite3.DatabaseError` propagated out of main.py and the user
    saw only "backend didn't start in time". Now the bad file is moved to a
    timestamped `.corrupt-` sibling — kept, never deleted, so its data can still
    be recovered — and a fresh database opens in its place.
    """
    path = tmp_path / "jarvis.sqlite3"
    path.write_bytes(b"this is not a sqlite database, just junk bytes \x00\x01\x02")

    with caplog.at_level(logging.WARNING):
        conn = db.connect(path)

    # (a) it opened successfully as a real, empty database.
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == (
        db.SCHEMA_VERSION
    )
    conn.close()

    # (b) the junk file was moved aside under a .corrupt- name, still present.
    backups = list(tmp_path.glob("jarvis.sqlite3.corrupt-*"))
    assert len(backups) == 1, f"expected exactly one .corrupt- backup, found {backups}"
    assert backups[0].read_bytes().startswith(b"this is not a sqlite database")
    # ...and the fresh file at the original path is a genuine SQLite file.
    assert path.exists()
    assert path.read_bytes().startswith(b"SQLite format 3")

    # (c) a WARNING naming both paths and the underlying error was logged.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected one warning, got {[r.message for r in warnings]}"
    msg = warnings[0].getMessage()
    assert str(path) in msg and str(backups[0]) in msg


def test_a_database_error_without_a_real_file_is_not_swallowed(monkeypatch):
    """The recovery is for corrupt *files*. If `:memory:` (or anything that
    isn't a file on disk) somehow raises DatabaseError, that is a real fault and
    must surface — there is nothing to rename, and hiding it would turn a bug
    into a silent fresh database."""

    def boom(*_a, **_k):
        raise sqlite3.DatabaseError("simulated open failure")

    monkeypatch.setattr(db, "_open", boom)
    with pytest.raises(sqlite3.DatabaseError):
        db.connect(":memory:")


def test_a_non_database_error_is_not_treated_as_corruption(tmp_path, monkeypatch):
    """The catch is `sqlite3.DatabaseError` on purpose, not bare `Exception`.

    A different error out of the open path — a genuine bug — must propagate, not
    be mistaken for a corrupt file and silently paper over with a fresh one. And
    nothing should be renamed: the file is not the problem."""
    path = tmp_path / "jarvis.sqlite3"
    path.write_bytes(b"junk")

    def boom(*_a, **_k):
        raise ValueError("a real bug, not corruption")

    monkeypatch.setattr(db, "_open", boom)
    with pytest.raises(ValueError):
        db.connect(path)
    leftover = list(tmp_path.glob("jarvis.sqlite3.corrupt-*"))
    assert leftover == [], "must not rename on a non-DB error"
