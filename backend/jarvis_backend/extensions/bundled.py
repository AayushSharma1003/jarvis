"""Extensions that ship with the app, and getting them into the data dir.

The loader only ever looks at `config.extensions_dir()` under the data dir, and
until M6.0 nothing put anything there -- so `timers-reminders`, built and
live-verified in M5.4, did not exist for any real user. This module is the
missing copy.

**Seeding delivers bytes; it does not bless them** -- the same posture as
`jarvis install` (security-model.md §5). A seeded extension lands as `pending`
and goes through the identical declaration prompt as a folder dropped in by
hand. Shipping a default is not consent to run it.

**Nothing is ever overwritten.** §5 forbids extension auto-update in as many
words ("an extension that can update itself is an extension whose approved
bytes are a suggestion"), and that applies to us too: a newer bundled version
of an extension the user already has must read as `changed` after they look at
it, not replace approved bytes behind their back. The content-keyed approval
already *is* the update mechanism.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# What actually ships. Explicit rather than "whatever is in the folder",
# because `extensions/calendar-macos` is a manifest with a 0-byte
# extension.py: seeding it would put an extension in every user's panel that
# fails the moment it is approved. It joins this tuple when it has code.
BUNDLED: tuple[str, ...] = ("timers-reminders",)


def bundled_source_dir() -> Path | None:
    """Locate the shipped extensions folder in both dev and frozen layouts.

    Mirrors `llm/catalog.py`'s `catalog_path()`, which solves exactly this
    problem for the model catalog: PyInstaller onedir puts data files under
    sys._MEIPASS, and from source the folder is at the repo root.
    """
    candidates = []
    if bundle := getattr(sys, "_MEIPASS", None):
        candidates.append(Path(bundle) / "extensions")
    # extensions/ -> jarvis_backend/ -> backend/ -> repo root
    candidates.append(Path(__file__).resolve().parents[3] / "extensions")
    return next((p for p in candidates if p.is_dir()), None)


def seed_bundled_extensions(dest: Path, source: Path | None = None) -> list[str]:
    """Copy any missing bundled extension into `dest`. Returns what was copied.

    Never raises for an absent source: a checkout or a bundle without the
    folder is a reason to have no default extensions, not a reason for the
    sidecar to fail to boot.
    """
    source = source if source is not None else bundled_source_dir()
    if source is None:
        # Only None needs its own branch (`None / name` would raise). A source
        # path that merely doesn't exist is already handled by the per-name
        # `origin.is_dir()` below — a `not source.is_dir()` test here was
        # decoration, and mutation testing said so.
        return []

    seeded: list[str] = []
    for name in BUNDLED:
        origin = source / name
        target = dest / name
        # `exists`, not `is_dir`: anything already sitting at that name is the
        # user's, and replacing it is the auto-update this must not be.
        if not origin.is_dir() or target.exists():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            origin,
            target,
            symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        seeded.append(name)
    return seeded
