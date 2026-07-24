"""Voice exchange orchestration over the WebSocket, with fake hardware/models.

Drives the full state machine — listening → transcribing → thinking →
speaking → idle — through the real server dispatch, VoiceSession, endpointer,
and chunker. No microphone, speaker, or model files involved.
"""

from __future__ import annotations

import asyncio
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jarvis_backend.config import Config
from jarvis_backend.llm.base import TextDelta
from jarvis_backend.server.app import AppState, create_app
from jarvis_backend.storage import db
from jarvis_backend.storage.conversations import Store
from jarvis_backend.stt.endpointing import Endpointer
from jarvis_backend.stt.vad import CHUNK_SAMPLES
from tests.test_ws import TOKEN, FakeBackend, connect, curated  # noqa: F401 (fixture)

SILENCE = np.zeros(CHUNK_SAMPLES, dtype=np.float32)
SPEECH = np.full(CHUNK_SAMPLES, 0.8, dtype=np.float32)


class FakeCapture:
    def __init__(self, script: list[np.ndarray]):
        self._script = script
        self._backlog: list[np.ndarray] = []
        self.closed = False

    def start(self) -> None:
        pass

    def feed_backlog(self, chunks: list[np.ndarray]) -> None:
        """Audio that arrived while nothing was reading the stream."""
        self._backlog.extend(chunks)

    def backlog(self) -> list[np.ndarray]:
        out, self._backlog = self._backlog, []
        return out

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
        # The real Player.stop() only *clears* the buffer; the stream stays open
        # and anything enqueued afterwards plays. So "was anything handed to the
        # player after a barge-in" is the question that matters, not "was stop
        # called" — see test_barge_in_does_not_speak_a_sentence_synthesized_after_the_stop.
        self.enqueued_after_stop: list[np.ndarray] = []

    def start(self) -> None:
        pass

    def enqueue(self, samples: np.ndarray) -> None:
        self.enqueued.append(samples)
        if self.stopped:
            self.enqueued_after_stop.append(samples)

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
    def _make(voice_io, backend=None, registry=None, confirm=None):
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
            registry=registry,
            confirm=confirm,
        )
        if confirm is not None:
            confirm.bind(lambda: state.connections)
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


class SlowLoadVoiceIO(FakeVoiceIO):
    """The engines take real time to load and the user talks straight through it.

    load() is where the first exchange after app start spends ~2.5 s (whisper's
    Metal shaders, Kokoro's graph). Anything said in that window only survives
    if the mic was opened *before* the load, so the audio is sitting in the
    buffer by the time we start reading.
    """

    def __init__(self, spoken_during_load: list[np.ndarray], script, **kw):
        super().__init__(script, **kw)
        self._spoken_during_load = spoken_during_load

    def load(self) -> None:
        assert self.captures, "the mic must be open before the engines load"
        self.captures[-1].feed_backlog(self._spoken_during_load)


def test_speech_during_engine_load_is_not_clipped(make_voice_client):
    # The whole utterance lands while load() runs; the live stream is silence.
    io = SlowLoadVoiceIO(utterance_script(), [SILENCE] * 200, max_wait_ms=640)
    client, _ = make_voice_client(io)
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        msgs = drain_voice(ws)

    assert [m for m in msgs if m["type"] == "error"] == []
    assert [m for m in msgs if m["type"] == "stt.text"] == [
        {"type": "stt.text", "text": "hello jarvis"}
    ]


def test_silent_load_does_not_spend_the_no_speech_budget(make_voice_client):
    # Room tone during a long load must not count against the listening window:
    # the user hasn't been shown "listening" yet. 100 chunks of backlog is well
    # past max_wait, so a naive replay would time out before they could speak.
    io = SlowLoadVoiceIO([SILENCE] * 100, utterance_script(), max_wait_ms=640)
    client, _ = make_voice_client(io)
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        msgs = drain_voice(ws)

    assert [m for m in msgs if m["type"] == "stt.text"] == [
        {"type": "stt.text", "text": "hello jarvis"}
    ]


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


class SpeakThenHangBackend(FakeBackend):
    """A complete sentence — so TTS runs and audio is queued — then hangs.
    That is the state a user barges in on: Jarvis talking, model still going."""

    async def stream_chat(self, model, messages, tools=None):
        yield TextDelta("This is the reply.")
        yield TextDelta(" And more to come")
        await asyncio.sleep(3600)


def test_stop_while_the_model_is_still_streaming_silences_playback(make_voice_client):
    """**Barge-in must work while the model is still generating, not only after.**

    `run_exchange` absorbs CancelledError so it can persist the partial turn
    (the delete-races-the-generation guard needs that), so a stop raised during
    generation comes back as an ordinary `ExchangeResult` — and the exchange
    used to carry straight on to `await synth_task` and `player.drain()`,
    speaking the entire queued reply to a user who had just interrupted it and
    reporting `reason="done"`.

    It hid because the barge-in that *was* verified acoustically happens after
    streaming ends, where the task is parked in `await synth_task` and asyncio
    cancels that inner task for us. The window is the whole time the model is
    still talking — the first seconds of every reply, and far longer on a slow
    model.

    Worse than the audio: `handle_wake` does `await conn.cancel_generation()`
    before broadcasting, so the wake word stayed dead for the length of the
    reply it failed to interrupt.
    """
    io = FakeVoiceIO(utterance_script())
    client, _ = make_voice_client(io, backend=SpeakThenHangBackend())
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        while True:
            m = ws.receive_json()
            if m["type"] == "voice.state" and m["state"] == "speaking":
                break
        ws.send_json({"type": "voice.stop"})
        msgs = drain_voice(ws, until_reasons=("done", "stopped", "error", "no_speech"))

    assert io.player_.stopped, "barge-in during generation did not silence the player"
    final = [m for m in msgs if m["type"] == "voice.state" and m["state"] == "idle"][-1]
    assert final.get("reason") == "stopped", f"reported {final.get('reason')}, not an interrupt"
    # chat.done must still go out, or the frontend keeps `streamKey` set and the
    # composer stays disabled with nothing coming to clear it.
    assert any(m["type"] == "chat.done" for m in msgs), "no chat.done — streamKey would leak"


def test_barge_in_does_not_speak_a_sentence_synthesized_after_the_stop(make_voice_client):
    """**Barge-in must actually silence Jarvis, including the sentence in flight.**

    `_synth_worker` is a separate task, so cancelling the exchange does not
    cancel it. Parked in `to_thread(io.synthesize, ...)` it finishes that
    synthesis and calls `player.enqueue()` — *after* the barge-in handler
    already called `player.stop()`. `Player.stop()` only clears the buffer and
    deliberately leaves the stream open (audio/playback.py), so the late enqueue
    refills it and the assistant speaks one more sentence after being told to
    stop.

    The window is not exotic: the exchange spends the end of every spoken reply
    parked at `await synth_task` while the last sentence is synthesized, which
    is exactly when a user interrupts an answer they don't like.
    """
    started, release = threading.Event(), threading.Event()

    class ReleasingPlayer(FakePlayer):
        """Frees the blocked synthesis at the instant of the barge-in.

        `stop()` is called in the handler between `synth_task.cancel()` and the
        `await send(...)`, so this lands the worker's resumption inside that
        await — the exact window the handler-level cancel exists to close, and
        the one the `finally` sweep is too late for. Without a deterministic
        trigger the race is unhittable from a test."""

        def stop(self) -> None:
            super().stop()
            release.set()

    class BlockingSynthIO(FakeVoiceIO):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.player_ = ReleasingPlayer()

        def synthesize(self, text: str):
            started.set()
            release.wait(5)  # hold the worker inside to_thread
            return super().synthesize(text)

    # The model must still be streaming, or the exchange parks in
    # `await synth_task` and asyncio cancels the worker for us — which is why
    # this hid behind the barge-in that was verified acoustically.
    io = BlockingSynthIO(utterance_script())
    client, _ = make_voice_client(io, backend=SpeakThenHangBackend())
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        assert started.wait(5), "never reached synthesis"
        ws.send_json({"type": "voice.stop"})
        drain_voice(ws, until_reasons=("stopped",))
        assert io.player_.stopped
        # Round-trip so the loop is guaranteed turns in which the freed worker
        # could resume and enqueue, if it were still going to.
        for _ in range(2):
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

    assert io.player_.enqueued_after_stop == [], (
        "audio was handed to the player after barge-in silenced it"
    )


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


# -- confirmation in a spoken turn (M4.2) -----------------------------------


def _confirming_voice_client(make_voice_client, io, *, timeout=5.0):
    """A spoken turn whose one tool needs confirmation."""
    from jarvis_backend.llm.base import ToolCall
    from jarvis_backend.security.confirm import ConfirmBroker
    from jarvis_backend.security.permissions import ASK, PermissionGate
    from jarvis_backend.tools.registry import Registry
    from tests.test_ws import ToolOnceBackend

    broker = ConfirmBroker(timeout=timeout)
    registry = Registry(PermissionGate(broker))
    registry.register(lambda text: f"echoed {text}", risk=ASK, name="echo", description="d")
    client, state = make_voice_client(
        io,
        backend=ToolOnceBackend(ToolCall("c1", "echo", {"text": "hi"})),
        registry=registry,
        confirm=broker,
    )
    return client, state, broker


def _await_confirm(ws):
    while (m := ws.receive_json())["type"] != "confirm.request":
        pass
    return m


def test_a_spoken_tool_turn_asks_for_confirmation(make_voice_client, curated):  # noqa: F811
    """The voice path shares run_exchange, so the gate applies identically —
    a spoken request cannot run an `ask` tool without a dialog either."""
    io = FakeVoiceIO(utterance_script(), transcript="echo hi")
    client, _, _ = _confirming_voice_client(make_voice_client, io)
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        req = _await_confirm(ws)
        # The dialog knows this is a spoken turn, which is what lets the UI
        # decide to ask the backend to say so out loud.
        assert req["voice"] is True
        ws.send_json({"type": "confirm.respond", "id": req["id"], "answer": "once"})
        msgs = drain_voice(ws)
    span = next(m for m in msgs if m["type"] == "tool.span")
    assert span["ok"] is True
    assert span["content"] == "echoed hi"


def test_voice_say_speaks_a_line_the_frontend_wrote(make_voice_client, curated):  # noqa: F811
    """The i18n rule and TTS pull in opposite directions: the backend must not
    author English, but it owns the speaker. So the frontend sends the sentence
    and the backend only synthesizes it — this is how "I need your OK — check
    the window" gets spoken without a word of copy in Python.

    Driven from the parked confirm on purpose: that is the only moment the
    prompt is useful, and the only moment the synth worker is reliably still
    waiting rather than already drained.
    """
    prompt = "I need your OK — check the window."
    io = FakeVoiceIO(utterance_script(), transcript="echo hi")
    client, _, _ = _confirming_voice_client(make_voice_client, io, timeout=30.0)
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        req = _await_confirm(ws)
        ws.send_json({"type": "voice.say", "text": prompt})
        # Give the dispatcher a turn to route it before the exchange resumes.
        ws.send_json({"type": "ping"})
        while ws.receive_json()["type"] != "pong":
            pass
        ws.send_json({"type": "confirm.respond", "id": req["id"], "answer": "once"})
        drain_voice(ws)
    assert prompt in io.synthesized


def test_voice_say_outside_a_spoken_turn_is_ignored(make_voice_client):
    """No live exchange means no player and nothing to interrupt. It must be a
    no-op, not an error and certainly not a crash."""
    io = FakeVoiceIO(utterance_script())
    client, _ = make_voice_client(io)
    with connect(client) as ws:
        ws.send_json({"type": "voice.say", "text": "nobody is listening"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
    assert io.synthesized == []


def test_voice_say_is_released_when_the_turn_ends(make_voice_client):
    """The queue handle must not outlive the exchange, or a later voice.say
    would push into a dead turn's queue and be silently swallowed."""
    io = FakeVoiceIO(utterance_script())
    client, state = make_voice_client(io)
    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        drain_voice(ws)
        assert state.connections[0].voice_sentences is None
        ws.send_json({"type": "voice.say", "text": "too late"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
    assert "too late" not in io.synthesized
