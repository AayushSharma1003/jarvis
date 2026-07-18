"""Voice exchange orchestration over the WebSocket, with fake hardware/models.

Drives the full state machine — listening → transcribing → thinking →
speaking → idle — through the real server dispatch, VoiceSession, endpointer,
and chunker. No microphone, speaker, or model files involved.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jarvis_backend.config import Config
from jarvis_backend.server.app import AppState, create_app
from jarvis_backend.storage import db
from jarvis_backend.storage.conversations import Store
from jarvis_backend.stt.endpointing import Endpointer
from jarvis_backend.stt.vad import CHUNK_SAMPLES
from tests.test_ws import TOKEN, FakeBackend, connect

SILENCE = np.zeros(CHUNK_SAMPLES, dtype=np.float32)
SPEECH = np.full(CHUNK_SAMPLES, 0.8, dtype=np.float32)


class FakeCapture:
    def __init__(self, script: list[np.ndarray]):
        self._script = script
        self.closed = False

    def start(self) -> None:
        pass

    async def chunks(self):
        for c in self._script:
            yield c
            await asyncio.sleep(0)
        await asyncio.Event().wait()  # a real mic never ends; block like one

    def close(self) -> None:
        self.closed = True


class FakePlayer:
    samplerate = 24_000
    level = 0.42

    def __init__(self):
        self.enqueued: list[np.ndarray] = []
        self.stopped = False

    def start(self) -> None:
        pass

    def enqueue(self, samples: np.ndarray) -> None:
        self.enqueued.append(samples)

    @property
    def pending(self) -> int:
        return 0

    def stop(self) -> None:
        self.stopped = True

    async def drain(self) -> None:
        pass


class FakeVoiceIO:
    def __init__(self, script: list[np.ndarray], transcript="hello jarvis", max_wait_ms=2_000):
        self._script = script
        self._transcript = transcript
        self._max_wait_ms = max_wait_ms
        self.player_ = FakePlayer()
        self.captures: list[FakeCapture] = []
        self.synthesized: list[str] = []

    def load(self) -> None:
        pass

    def open_capture(self) -> FakeCapture:
        cap = FakeCapture(self._script)
        self.captures.append(cap)
        return cap

    def player(self) -> FakePlayer:
        return self.player_

    def vad_prob(self, chunk: np.ndarray) -> float:
        return 0.95 if float(np.abs(chunk).mean()) > 0.1 else 0.02

    def transcribe(self, audio: np.ndarray) -> str:
        assert audio.dtype == np.float32 and audio.size > 0
        return self._transcript

    def synthesize(self, text: str):
        self.synthesized.append(text)
        return np.full(240, 0.1, dtype=np.float32), 24_000

    def make_endpointer(self) -> Endpointer:
        return Endpointer(
            min_speech_ms=64, min_silence_ms=128, pre_roll_ms=64, max_wait_ms=self._max_wait_ms
        )


def utterance_script() -> list[np.ndarray]:
    return [SILENCE] * 5 + [SPEECH] * 6 + [SILENCE] * 8


@pytest.fixture
def make_voice_client(tmp_path):
    def _make(voice_io, backend=None):
        state = AppState(
            token=TOKEN,
            store=Store(db.connect(":memory:")),
            backend=backend or FakeBackend(chunks=("This is the reply", ". It has two parts.")),
            config=Config(
                ollama_url="http://unused",
                default_model="",
                config_path=tmp_path / "c.toml",
                data_dir=tmp_path,
            ),
            voice_io=voice_io,
        )
        return TestClient(create_app(state)), state

    return _make


def drain_voice(ws, until_reasons=("done", "no_speech", "stopped", "error")):
    """Collect messages until voice.state idle with one of the given reasons."""
    msgs = []
    while True:
        msg = ws.receive_json()
        msgs.append(msg)
        if msg["type"] == "voice.state" and msg["state"] == "idle":
            if msg.get("reason") in until_reasons:
                return msgs


def states(msgs):
    return [m["state"] for m in msgs if m["type"] == "voice.state"]


def test_full_voice_exchange(make_voice_client):
    io = FakeVoiceIO(utterance_script())
    client, state = make_voice_client(io)
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        msgs = drain_voice(ws)

    seq = states(msgs)
    assert seq[0] == "loading"
    for a, b in [("listening", "transcribing"), ("transcribing", "thinking"),
                 ("thinking", "speaking"), ("speaking", "idle")]:
        assert seq.index(a) < seq.index(b), f"{a} must precede {b} in {seq}"

    stt = [m for m in msgs if m["type"] == "stt.text"]
    assert stt == [{"type": "stt.text", "text": "hello jarvis"}]

    deltas = "".join(m["text"] for m in msgs if m["type"] == "chat.delta")
    assert deltas == "This is the reply. It has two parts."
    done = next(m for m in msgs if m["type"] == "chat.done")

    # Spoken sentences cover the reply, and audio reached the player.
    assert " ".join(io.synthesized) == "This is the reply. It has two parts."
    assert io.player_.enqueued
    assert io.captures[0].closed

    # The spoken turn persisted exactly like a typed one.
    turns = state.store.path(done["conversation_id"])
    assert turns[-1].messages[0].content == "hello jarvis"
    assert turns[-1].messages[1].content == "This is the reply. It has two parts."


def test_no_speech_times_out(make_voice_client):
    io = FakeVoiceIO([SILENCE] * 200, max_wait_ms=320)
    client, _ = make_voice_client(io)
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        msgs = drain_voice(ws)
    idle = [m for m in msgs if m["type"] == "voice.state" and m["state"] == "idle"]
    assert idle[-1]["reason"] == "no_speech"
    assert not any(m["type"] == "stt.text" for m in msgs)
    assert io.synthesized == []


def test_empty_transcription_goes_idle(make_voice_client):
    io = FakeVoiceIO(utterance_script(), transcript="")
    client, _ = make_voice_client(io)
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        msgs = drain_voice(ws)
    assert msgs[-1].get("reason") == "no_speech"
    assert not any(m["type"] == "chat.start" for m in msgs)


def test_voice_stop_interrupts(make_voice_client):
    # Never-ending silence with timeouts disabled: only voice.stop can end it.
    io = FakeVoiceIO([SILENCE] * 10_000, max_wait_ms=None)
    client, _ = make_voice_client(io)
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        # Wait until we're definitely listening.
        while True:
            m = ws.receive_json()
            if m["type"] == "voice.state" and m["state"] == "listening":
                break
        ws.send_json({"type": "voice.stop"})
        drain_voice(ws, until_reasons=("stopped",))
    assert io.player_.stopped


def test_voice_start_while_busy_is_refused(make_voice_client):
    io = FakeVoiceIO([SILENCE] * 10_000, max_wait_ms=None)
    client, _ = make_voice_client(io)
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        ws.send_json({"type": "voice.start"})
        while True:
            m = ws.receive_json()
            if m["type"] == "error":
                assert m["code"] == "BUSY"
                break
        ws.send_json({"type": "voice.stop"})
        drain_voice(ws, until_reasons=("stopped",))


def test_voice_unavailable_without_io(make_voice_client):
    client, _ = make_voice_client(None)
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        assert ws.receive_json() == {"type": "error", "code": "VOICE_UNAVAILABLE"}
