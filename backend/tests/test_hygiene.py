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


# -- every code the UI can be handed has words for it -------------------------

import json  # noqa: E402
import re  # noqa: E402

EN_JSON = PACKAGE.parent.parent / "app" / "src" / "i18n" / "en.json"

# The exception types the WebSocket layer converts straight into an `error`
# frame with `protocol.error(e.code, ...)`. Derived by reading server/app.py and
# server/voice.py's handlers; if a new one is added there it belongs here too,
# which is why the test below also checks that list against the source.
FORWARDED = ("StorageError", "LLMError", "WakeError", "AudioError", "STTError", "TTSError")


def _en() -> dict:
    return json.loads(EN_JSON.read_text(encoding="utf-8"))


def _codes_reaching_the_error_frame() -> dict[str, str]:
    """CODE → where it comes from, for everything that can land in `error.*`."""
    found: dict[str, str] = {}
    for path in PACKAGE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(PACKAGE).as_posix()
        for m in re.finditer(r'protocol\.error\(\s*"([A-Z0-9_]+)"', text):
            found[m.group(1)] = rel
        for exc in FORWARDED:
            for m in re.finditer(rf'raise {exc}\(\s*"([A-Z0-9_]+)"', text):
                found[m.group(1)] = f"{rel} ({exc})"
    return found


def test_every_code_that_reaches_the_error_banner_has_words():
    """The i18n rule is that the backend emits codes and the frontend owns the
    wording. A code with no key still renders — `errorText` degrades to
    "Something went wrong (TURN_NOT_FOUND)" — which keeps the rule and still
    shows a stranger a SCREAMING_SNAKE identifier they can do nothing with.

    Derived from the source rather than listed here, for gotcha 30's reason: a
    hardcoded expectation is written once against today's code and then quietly
    stops covering whatever is added next. This is the check that would have
    noticed the four StorageError codes that had no key for five phases.
    """
    have = set(_en()["error"])
    missing = {c: src for c, src in _codes_reaching_the_error_frame().items() if c not in have}
    assert missing == {}, (
        f"codes the UI can be handed with no `error.*` key: {missing}. "
        "Add them to app/src/i18n/en.json."
    )


def test_the_forwarded_exception_list_still_matches_the_server():
    """FORWARDED above is hand-maintained, so it rots silently and takes the
    coverage of the test above with it. Pin it: every exception type the WS
    handlers convert with `protocol.error(e.code…)` must be listed."""
    server = (PACKAGE / "server").rglob("*.py")
    caught: set[str] = set()
    for path in server:
        text = path.read_text(encoding="utf-8")
        # `except (A, B) as e:` / `except A as e:` immediately followed within
        # a few lines by protocol.error(e.code
        for m in re.finditer(r"except \(?([A-Za-z, ]+?)\)? as e:\n(.{0,400}?)\n\n", text, re.S):
            if "protocol.error(e.code" in m.group(2):
                caught.update(n.strip() for n in m.group(1).split(",") if n.strip())
    unlisted = {c for c in caught if c.endswith("Error")} - set(FORWARDED) - {"Exception"}
    assert unlisted == set(), (
        f"server handlers forward {unlisted} to the error frame, but FORWARDED "
        "in this file does not list them, so their codes are unchecked."
    )
