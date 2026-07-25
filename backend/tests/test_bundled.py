"""Seeding the extensions that ship with the app into the data directory.

Nothing did this before M6.0, so `timers-reminders` -- built and live-verified
in M5.4 -- did not exist for any real user: the loader only ever reads
`extensions_dir()` under the data dir, and the installer never put anything
there.

The two properties that carry this are inherited from security-model.md §5,
not invented here:

  * seeded extensions land as **pending**. Shipping a default does not approve
    it; §5's informed consent is the whole point, and a bundled extension is
    someone's code running as the user exactly like an installed one.
  * **never overwrite**. An extension that updates itself is an extension whose
    approved bytes are a suggestion (§5, in as many words). A newer bundled
    version simply hashes differently and reads as `changed`.
"""

from __future__ import annotations

from jarvis_backend.extensions.approvals import ApprovalStore
from jarvis_backend.extensions.bundled import BUNDLED, seed_bundled_extensions
from jarvis_backend.extensions.loader import discover


def _make_source(root, name: str, body: str = "def noop():\n    return 'x'\n"):
    d = root / name
    d.mkdir(parents=True)
    (d / "extension.py").write_text(body)
    (d / "manifest.toml").write_text(
        f'[extension]\nname = "{name}"\nversion = "0.1.0"\n'
        'description = "seeded"\n\n[permissions]\nnetwork = false\n\n'
        '[[tools]]\nname = "noop"\nrisk = "safe"\n'
    )
    return d


def test_a_bundled_extension_is_copied_into_the_data_dir(tmp_path):
    source = tmp_path / "src"
    _make_source(source, BUNDLED[0])
    dest = tmp_path / "data" / "extensions"

    seeded = seed_bundled_extensions(dest, source)

    assert seeded == [BUNDLED[0]]
    assert (dest / BUNDLED[0] / "extension.py").is_file()
    assert (dest / BUNDLED[0] / "manifest.toml").is_file()


def test_a_seeded_extension_is_pending_not_approved(tmp_path):
    """Shipping a default must not approve it -- §5's informed consent."""
    source = tmp_path / "src"
    _make_source(source, BUNDLED[0])
    dest = tmp_path / "data" / "extensions"
    seed_bundled_extensions(dest, source)

    store = ApprovalStore(tmp_path / "data" / "extensions.toml")
    found = discover(dest, store)

    assert [(e.name, e.status) for e in found] == [(BUNDLED[0], "pending")]


def test_seeding_never_overwrites_what_is_already_there(tmp_path):
    """§5: no extension auto-update, ever. New bytes must read as `changed`
    after a human looks, not replace approved ones behind their back."""
    source = tmp_path / "src"
    _make_source(source, BUNDLED[0], body="def noop():\n    return 'NEW'\n")
    dest = tmp_path / "data" / "extensions"
    existing = dest / BUNDLED[0]
    existing.mkdir(parents=True)
    (existing / "extension.py").write_text("def noop():\n    return 'OLD'\n")

    seeded = seed_bundled_extensions(dest, source)

    assert seeded == []
    assert (existing / "extension.py").read_text() == "def noop():\n    return 'OLD'\n"


def test_only_named_extensions_are_seeded(tmp_path):
    """The list is explicit so a stub cannot ship by sitting in the folder.

    calendar-macos is a manifest with a 0-byte extension.py; seeding it would
    put an extension in every user's panel that fails the moment it is
    approved.
    """
    source = tmp_path / "src"
    _make_source(source, BUNDLED[0])
    _make_source(source, "not-in-the-list")
    dest = tmp_path / "data" / "extensions"

    seeded = seed_bundled_extensions(dest, source)

    assert seeded == [BUNDLED[0]]
    assert not (dest / "not-in-the-list").exists()


def test_a_missing_source_is_survivable(tmp_path):
    """A source build without the folder must not stop the sidecar booting."""
    dest = tmp_path / "data" / "extensions"
    assert seed_bundled_extensions(dest, tmp_path / "nope") == []
    assert not dest.exists()


def test_an_unlocatable_source_is_survivable(tmp_path, monkeypatch):
    """The `source is None` branch, which is the only one that has to exist.

    Separate from the missing-directory test above because they are different
    branches: this one would raise TypeError on `None / name` without its
    guard, while a merely-absent directory is caught per-name inside the loop.
    """
    monkeypatch.setattr(
        "jarvis_backend.extensions.bundled.bundled_source_dir", lambda: None
    )
    assert seed_bundled_extensions(tmp_path / "data" / "extensions") == []


def test_pycache_is_not_copied(tmp_path):
    """__pycache__ is excluded from the approval digest; copying it in is noise
    that also risks a stale .pyc travelling with the extension."""
    source = tmp_path / "src"
    d = _make_source(source, BUNDLED[0])
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "extension.cpython-313.pyc").write_bytes(b"stale")
    dest = tmp_path / "data" / "extensions"

    seed_bundled_extensions(dest, source)

    assert not (dest / BUNDLED[0] / "__pycache__").exists()
