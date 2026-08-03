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
