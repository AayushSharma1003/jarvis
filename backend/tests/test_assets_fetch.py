"""Downloading the voice models from inside the app.

Why this exists: the models are ~500 MB and are fetched by explicit user action
only (zero phone-home). That was implemented as `scripts/fetch_models.py` --
which is NOT in the packaged bundle, and the frozen sidecar has no CLI, its
entrypoint being the server. So every user who installed from a release release
was told by the app itself to run `uv run python ../scripts/fetch_models.py`,
naming a repo they do not have and a tool they have not installed. Voice --
wake word, speech in, speech out -- was unreachable for all of them.

The download therefore has to be something the running backend can do. It is
still explicit user action: nothing fetches until a `assets.fetch` message
arrives, which is a button press.
"""

from __future__ import annotations

import hashlib

import pytest

from jarvis_backend import assets
from tests.test_ws import TOKEN, connect, make_client  # noqa: F401 (fixtures)

PAYLOAD = b"x" * 4096
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class FakeResponse:
    """Minimal urlopen() stand-in: enough of the contract that a real HTTP
    swap stays honest (status for resume, read(n) for streaming)."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status
        self._pos = 0

    def read(self, n: int) -> bytes:
        chunk = self._body[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def one_asset(tmp_path, monkeypatch):
    """A single tiny asset in a scratch models dir — no network, no 500 MB."""
    asset = assets.Asset(
        name="tiny",
        filename="tiny.bin",
        url="https://example.invalid/tiny.bin",
        size_bytes=len(PAYLOAD),
        sha256=DIGEST,
        group="voice",
    )
    monkeypatch.setattr(assets, "ASSETS", {"tiny": asset})
    monkeypatch.setattr(assets, "models_dir", lambda: tmp_path / "models")
    return asset


def test_download_writes_verifies_and_reports_progress(one_asset):
    seen: list[tuple[str, int, int]] = []
    assets.download(
        one_asset,
        on_progress=lambda name, done, total: seen.append((name, done, total)),
        opener=lambda req, timeout=0: FakeResponse(PAYLOAD),
    )
    dest = assets.path_for("tiny")
    assert dest.read_bytes() == PAYLOAD
    assert assets.is_present("tiny")
    assert seen and seen[-1][1] == len(PAYLOAD)
    assert not list(dest.parent.glob("*.part")), "a .part file survived a successful download"


def test_a_corrupted_download_is_refused_and_leaves_no_model(one_asset):
    """sha256 is pinned so an upstream swap or a truncated body cannot become a
    model file. Failing loudly beats a mystery crash inside onnxruntime later.
    """
    with pytest.raises(assets.AssetError) as e:
        assets.download(
            one_asset,
            opener=lambda req, timeout=0: FakeResponse(b"y" * len(PAYLOAD)),
        )
    assert e.value.code == "ASSET_CHECKSUM_MISMATCH"
    assert not assets.path_for("tiny").exists()
    assert not assets.is_present("tiny")
    # And the bad bytes are gone, not just unpromoted. A full-size `.part` that
    # survives is ~300 MB of disk holding a file we already know is wrong, and
    # it makes the next attempt's resume logic reason about garbage.
    leftover = list(assets.models_dir().glob("*.part"))
    assert leftover == [], f"a known-bad partial download survived: {leftover}"


def test_fetch_over_the_websocket_reports_progress_and_finishes(make_client, one_asset):  # noqa: F811
    """The whole point: a packaged user with no repo and no terminal can get
    the models."""
    client, _ = make_client()
    import jarvis_backend.server.app as app_mod

    app_mod._ASSET_OPENER = lambda req, timeout=0: FakeResponse(PAYLOAD)
    try:
        with connect(client) as ws:
            ws.send_json({"type": "assets.fetch"})
            progress, done = [], None
            while True:
                msg = ws.receive_json()
                if msg["type"] == "assets.progress":
                    progress.append(msg)
                elif msg["type"] == "assets.done":
                    done = msg
                    break
    finally:
        app_mod._ASSET_OPENER = None

    assert done is not None and done["ok"] is True
    assert progress, "no progress was reported for a multi-megabyte download"
    assert assets.is_present("tiny")


def test_fetch_reports_failure_without_crashing_the_connection(make_client, one_asset):  # noqa: F811
    """A dead network is normal. It must surface as a code, not a dropped WS."""
    client, _ = make_client()
    import jarvis_backend.server.app as app_mod

    def boom(req, timeout=0):
        raise OSError("network unreachable")

    app_mod._ASSET_OPENER = boom
    try:
        with connect(client) as ws:
            ws.send_json({"type": "assets.fetch"})
            while (msg := ws.receive_json())["type"] != "assets.done":
                pass
            assert msg["ok"] is False
            assert msg["failed"] == ["tiny"]
            # The connection is still usable. Drain past the refreshed
            # readiness the handler broadcasts on completion — it is sent even
            # on failure, which is the point: the gate must re-state that voice
            # is still unavailable rather than leave a stale row on screen.
            ws.send_json({"type": "ping"})
            while (msg := ws.receive_json())["type"] != "pong":
                assert msg["type"] == "readiness"
    finally:
        app_mod._ASSET_OPENER = None


# -- what a hostile or broken server can do to the disk -----------------------
#
# The sha256 is pinned precisely because upstream is not trusted to serve the
# right bytes. That distrust has to extend to *how many* bytes: the checksum is
# only ever consulted after the body is on disk, so a server that never stops
# sending fills the user's disk long before anything verifies it. There is no
# cancel button, the models dir is ~500 MB of expected traffic, and the target
# machine is an 8 GB laptop. The size ceiling is the same argument as
# `tools/shell.py`'s output cap and `tools/web.py`'s 512 KB read cap, applied to
# the one path in the app that writes a large file.


def test_a_server_that_overruns_the_expected_size_is_cut_off(one_asset, tmp_path):
    """A body that keeps coming is stopped at the declared size, not swallowed
    whole and measured afterwards."""
    # Comfortably more than one CHUNK, or the read loop finishes in a single
    # pass and the assertion below passes without any ceiling existing.
    flood = b"x" * (assets.CHUNK * 16)
    assert len(flood) > len(PAYLOAD) + assets.CHUNK, "the flood must outrun one chunk"
    with pytest.raises(assets.AssetError) as e:
        assets.download(one_asset, opener=lambda req, timeout=0: FakeResponse(flood))
    assert e.value.code == "ASSET_SIZE_MISMATCH"
    written = sum(p.stat().st_size for p in assets.models_dir().glob("*"))
    assert written <= len(PAYLOAD) + assets.CHUNK, (
        f"wrote {written} bytes for a {len(PAYLOAD)}-byte asset — "
        "an endless body is a disk-filling DoS with no cancel button"
    )


def test_an_overrun_leaves_no_partial_to_resume_from(one_asset):
    """The oversized bytes are deleted, not left for the next attempt to append
    to — a `.part` that is already too long can only ever fail again."""
    flood = b"x" * (assets.CHUNK * 16)
    with pytest.raises(assets.AssetError):
        assets.download(one_asset, opener=lambda req, timeout=0: FakeResponse(flood))
    assert list(assets.models_dir().glob("*.part")) == []


def test_no_space_is_reported_as_its_own_code_before_anything_is_written(one_asset, monkeypatch):
    """A nearly-full disk is an ordinary first-run state, and ENOSPC halfway
    through a 300 MB file reports "download failed" while silently keeping the
    partial bytes that helped fill the disk. Check first, say so plainly."""
    import shutil

    monkeypatch.setattr(
        shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 90, 10)
    )
    with pytest.raises(assets.AssetError) as e:
        assets.download(one_asset, opener=lambda req, timeout=0: FakeResponse(PAYLOAD))
    assert e.value.code == "ASSET_NO_SPACE"
    assert list(assets.models_dir().glob("*")) == [], "nothing should have been written"


# -- invariants over the asset table itself -----------------------------------
#
# Derived from ASSETS rather than restated as a list, for gotcha 30's reason: a
# hardcoded expectation is written once by someone looking at today's table and
# then silently stops covering whatever is added next.


def test_every_shipped_asset_pins_a_sha256():
    """`download` verifies the digest only `if asset.sha256` — an unpinned asset
    silently degrades to a size check, which any 147 MB file passes."""
    unpinned = [a.name for a in assets.ASSETS.values() if len(a.sha256) != 64]
    assert unpinned == [], f"assets with no pinned sha256: {unpinned}"


def test_every_shipped_asset_writes_a_bare_filename():
    """`download` joins `filename` onto the models dir. Nothing user-supplied
    reaches it today — the table is a frozen constant — but the next person to
    add an entry is the threat model, and `../../` in that field is a write
    anywhere the sidecar can reach."""
    import os
    from pathlib import Path

    for a in assets.ASSETS.values():
        assert a.filename == Path(a.filename).name, f"{a.name}: not a bare filename"
        assert not os.path.isabs(a.filename), f"{a.name}: absolute path"
        assert ".." not in a.filename and "\x00" not in a.filename, f"{a.name}: traversal"


def test_every_shipped_asset_is_fetched_over_https():
    """The digest makes plain http survivable, not acceptable: it would leak
    which models a user is downloading and hand a network attacker a free
    disk-filling retry loop."""
    bad = [a.name for a in assets.ASSETS.values() if not a.url.startswith("https://")]
    assert bad == [], f"assets not fetched over https: {bad}"


def test_the_test_seam_cannot_be_reached_from_outside_the_test_suite():
    """`_ASSET_OPENER` is a module global that replaces urlopen, so anything
    able to set it at runtime would choose where 500 MB is fetched from — and
    the sha256 pin is the only thing standing behind that.

    Nothing can: it is assigned once, to None, and otherwise only by tests. The
    check is derived from the source so it survives someone later deciding to
    make it configurable "for debugging", which is exactly the change that
    would turn a test seam into a download-source override.
    """
    from pathlib import Path

    pkg = Path(__file__).resolve().parent.parent / "jarvis_backend"
    # The statement, not a line number: pinning the line makes this fail on any
    # edit above it, which is noise rather than a finding.
    writes = [
        (p.relative_to(pkg).as_posix(), line.strip())
        for p in pkg.rglob("*.py")
        for line in p.read_text(encoding="utf-8").splitlines()
        if "_ASSET_OPENER" in line and "=" in line.split("_ASSET_OPENER", 1)[1][:3]
    ]
    assert writes == [("server/app.py", "_ASSET_OPENER = None")], (
        f"unexpected writes to the seam: {writes}"
    )

    import jarvis_backend.server.app as app_mod

    assert app_mod._ASSET_OPENER is None, "the seam is set outside a test"


def test_the_app_stays_usable_while_the_download_runs(make_client, one_asset):  # noqa: F811
    """A 500 MB download takes minutes and there is no cancel button, so if it
    holds the connection the user has no way out of a frozen app.

    It did. `assets.fetch` was awaited inline in the receive loop, so that
    connection read no further messages until the last byte landed: measured
    with a slow opener, a `ping` sent immediately after the first progress
    frame was answered only after `assets.done`. From the outside the app
    accepts typing, accepts ⌘M, accepts a click on another conversation, and
    does none of them — for the length of the download.
    """
    import time

    client, _ = make_client()
    import jarvis_backend.server.app as app_mod

    class Slow:
        def __init__(self):
            self.pos = 0

        def read(self, n):
            if self.pos >= len(PAYLOAD):
                return b""
            time.sleep(0.05)
            chunk = PAYLOAD[self.pos : self.pos + 256]
            self.pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    app_mod._ASSET_OPENER = lambda req, timeout=0: Slow()
    try:
        with connect(client) as ws:
            ws.send_json({"type": "assets.fetch"})
            assert ws.receive_json()["type"] == "assets.progress"  # it has begun
            ws.send_json({"type": "ping"})
            seen = []
            while (msg := ws.receive_json())["type"] != "pong":
                seen.append(msg["type"])
            assert "assets.done" not in seen, (
                "the ping was only answered after the download finished — the "
                "connection is blocked for the whole download"
            )
            # And it still finishes, on its own, without being waited on.
            while (msg := ws.receive_json())["type"] != "assets.done":
                pass
            assert msg["ok"] is True
    finally:
        app_mod._ASSET_OPENER = None


def test_download_progress_reaches_every_window(make_client, one_asset):  # noqa: F811
    """Broadcast, not sent back to the asker — the same rule wake.status and
    notifications follow, for the same reason: a webview reload replaces the
    connection, and a download that reports only to the one that started it
    would leave the new window showing a dead progress bar for two minutes."""
    client, _ = make_client()
    import jarvis_backend.server.app as app_mod

    app_mod._ASSET_OPENER = lambda req, timeout=0: FakeResponse(PAYLOAD)
    try:
        with connect(client) as asker, connect(client) as bystander:
            asker.send_json({"type": "assets.fetch"})
            while asker.receive_json()["type"] != "assets.done":
                pass
            # Read to a frame that is *guaranteed* to arrive rather than to a
            # fixed count: counting frames blocks forever when one fewer shows
            # up, which is a hang on a slow runner rather than a failure. (It
            # was: this test timed out on CI while passing locally.)
            seen = []
            while (kind := bystander.receive_json()["type"]) != "assets.done":
                seen.append(kind)
            # Specifically PROGRESS. An `or "assets.done"` here passed against an
            # implementation that sent progress to the asker alone, because the
            # completion frame was broadcast either way — the weaker assertion
            # tested the wrong half.
            assert "assets.progress" in seen, (
                f"a second window saw no download progress: {seen}"
            )
    finally:
        app_mod._ASSET_OPENER = None


def test_a_failed_download_can_be_retried(make_client, one_asset):  # noqa: F811
    """The single-flight guard has to be released by the task, not by the
    handler that started it — and a failed download is an ordinary state (a
    dropped connection produces one). A leaked guard answers ASSETS_BUSY
    forever, so the one thing the user would obviously try next, pressing the
    button again, is the one thing that can never work.
    """
    client, _ = make_client()
    import jarvis_backend.server.app as app_mod

    def offline(req, timeout=0):
        raise OSError("network unreachable")

    app_mod._ASSET_OPENER = offline
    try:
        with connect(client) as ws:
            ws.send_json({"type": "assets.fetch"})
            while (msg := ws.receive_json())["type"] != "assets.done":
                pass
            assert msg["ok"] is False

            # Now the network is back and the user presses the button again.
            app_mod._ASSET_OPENER = lambda req, timeout=0: FakeResponse(PAYLOAD)
            ws.send_json({"type": "assets.fetch"})
            seen = []
            while (msg := ws.receive_json())["type"] != "assets.done":
                seen.append(msg["type"])
            assert "error" not in seen, f"the retry was refused: {seen}"
            assert msg["ok"] is True
            assert assets.is_present("tiny")
    finally:
        app_mod._ASSET_OPENER = None
