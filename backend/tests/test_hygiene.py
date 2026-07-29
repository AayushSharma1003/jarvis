"""Repo hygiene the eye keeps missing.

This file holds invariants about the *shape* of the package rather than its
behaviour. There is one, and it has failed twice.

An empty `.py` file passes every check this project has: ruff is happy, pytest
collects nothing, the import graph is unaffected because nothing imports it.
It is invisible to all of them and visible to exactly one audience — a person
reading the repository, which since 2026-07-22 is the public. Seventeen were
deleted in M6.1 after an inventory that claimed fourteen; a later audit found
**seven more** in the backend, three of them empty files sitting next to the
real implementation under a name a maintainer would open first
(`agent/capabilities.py` beside `llm/capabilities.py`, `extensions/installer.py`
beside `extensions/install.py`, `server/websocket.py` beside `server/app.py`).

So the guard is here rather than in another sweep. A placeholder file is not a
plan; if something is deferred, `docs/architecture.md` is where it is recorded.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "jarvis_backend"


def test_no_empty_modules_in_the_package():
    """A 0-byte module is scaffolding that reads as a broken implementation.

    Scoped to `jarvis_backend/` deliberately. `.venv/`, `dist/` and `build/`
    are other people's trees or build output, and `extensions/calendar-macos/
    extension.py` at the repo root is a *documented* manifest-only reference
    (extensions/README.md, docs/extensions.md, C1) that `test_bundled.py`
    asserts is never seeded — the opposite of an accident.
    """
    # `__init__.py` is excluded because an empty one is the ordinary way to
    # mark a package; every one of them in this tree is exactly that.
    empty = sorted(
        p.relative_to(PACKAGE).as_posix()
        for p in PACKAGE.rglob("*.py")
        if p.name != "__init__.py" and p.stat().st_size == 0
    )
    assert empty == [], (
        f"empty module(s): {empty}. Delete them and record the deferral in "
        "docs/architecture.md — a 0-byte file was never the feature."
    )
