"""Wake-word service and barge-in, with fake detectors/mics (no hardware).

The vendored inference chain itself is validated two ways outside CI: score
parity against the openwakeword reference implementation, and the local-only
Kokoro round-trip test at the bottom (synthesize "Hey Jarvis" → detector must
trigger) which runs wherever the real models are fetched.
"""

from __future__ import annotations

import asyncio
import queue
import time

import numpy as np
import pytest

from jarvis_backend.config import Config, load_wake_enabled, save_wake_enabled
from jarvis_backend.server import protocol
from jarvis_backend.server.app import AppState, Connection, create_app, handle_wake
from jarvis_backend.storage import db
from jarvis_backend.storage.conversations import Store
from jarvis_backend.wake.detector import WakeError
from jarvis_backend.wake.pipeline import WakePipeline
from jarvis_backend.wake.service import WakeService
from tests.test_voice_ws import SILENCE, FakeVoiceIO, drain_voice, utterance_script
from tests.test_ws import TOKEN, FakeBackend, connect

# --- fakes ------------------------------------------------------------------


class FakePipeline:
    """Score is smuggled in the chunk itself: process([x]) → score x."""

    def reset(self) -> None:
        pass

    def process(self, chunk: np.ndarray) -> float | None:
        return float(chunk[0])


class SyncQueueCapture:
    def __init__(self, registry: list):
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self.closed = False
        registry.append(self)

    def get(self, timeout: float) -> np.ndarray:
        return self.q.get(timeout=timeout)

    def close(self) -> None:
        self.closed = True


def make_service(**overrides) -> tuple[WakeService, dict]:
    ctx: dict = {"captures": [], "wakes": 0, "persisted": []}

    async def on_wake() -> bool:
        ctx["wakes"] += 1
        return True

    kwargs = dict(
        make_pipeline=FakePipeline,
        open_capture=lambda: SyncQueueCapture(ctx["captures"]),
        on_wake=on_wake,
        persist=ctx["persisted"].append,
        enabled=True,
        threshold=0.5,
        available=True,
    )
    kwargs.update(overrides)
    return WakeService(**kwargs), ctx


async def until(cond, timeout=3.0):
    """The worker is a real thread; poll instead of counting loop turns."""
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


# --- the VAD gate (pipeline) ------------------------------------------------


class GateProbe:
    """Detector probe: records what audio reaches the chain; scores 0."""

    def __init__(self):
        self.resets = 0
        self.samples_fed = 0

    def reset(self) -> None:
        self.resets += 1

    def feed(self, chunk: np.ndarray) -> float | None:
        self.samples_fed += chunk.size
        return 0.0


def make_pipeline(vad_scores):
    probe = GateProbe()
    it = iter(vad_scores)
    calls = []

    def vad(_chunk):
        s = next(it, 0.0)
        calls.append(s)
        return s

    pipe = WakePipeline(vad, probe, preroll_s=0.096, hangover_s=0.096, idle_vad_stride=1)
    return pipe, probe, calls


SILENT = np.zeros(512, dtype=np.float32)  # below the RMS floor
AUDIBLE = np.full(512, 0.05, dtype=np.float32)


def test_true_silence_skips_even_the_vad():
    pipe, probe, vad_calls = make_pipeline(vad_scores=[])
    for _ in range(100):
        assert pipe.process(SILENT) is None
    assert vad_calls == []  # RMS floor short-circuits
    assert probe.samples_fed == 0  # and the chain certainly never ran


def test_gate_keeps_chain_asleep_below_vad_threshold():
    # 50, not more: sustained loud audio eventually raises the adaptive
    # noise floor past the test amplitude (by design — it's "the new room").
    pipe, probe, vad_calls = make_pipeline(vad_scores=[0.1] * 50)
    for _ in range(50):
        assert pipe.process(AUDIBLE) is None
    assert len(vad_calls) == 50  # audible → VAD judged it, said no
    assert probe.samples_fed == 0


def test_gate_replays_preroll_on_speech_onset():
    pipe, probe, _ = make_pipeline(vad_scores=[0.9])
    for _ in range(3):
        pipe.process(SILENT)  # buffered as preroll, VAD skipped
    resets_before = probe.resets
    pipe.process(AUDIBLE)  # speech onset
    assert probe.resets == resets_before + 1  # stale buffers cleared
    # preroll_s=0.096 (1536 samples) → lead-in + onset chunk, capped
    assert probe.samples_fed == 1536


def test_gate_sleeps_again_after_hangover():
    # speech for 2 chunks, then silence beyond the 0.096s (3-chunk) hangover
    pipe, probe, _ = make_pipeline(vad_scores=[0.9, 0.9] + [0.0] * 20)
    for _ in range(2):
        pipe.process(AUDIBLE)
    for _ in range(4):
        pipe.process(SILENT)  # active: silence still reaches the chain
    fed_at_sleep = probe.samples_fed
    for _ in range(10):
        assert pipe.process(SILENT) is None  # gated again
    assert probe.samples_fed == fed_at_sleep


# --- service ----------------------------------------------------------------


async def test_trigger_calls_on_wake_and_closes_capture_first(monkeypatch):
    monkeypatch.setattr("jarvis_backend.wake.service.COOLDOWN_S", 0.0)
    svc, ctx = make_service()
    svc.ensure_started()
    await until(lambda: ctx["captures"])
    cap = ctx["captures"][0]
    cap.q.put_nowait(np.array([0.2]))  # below threshold
    cap.q.put_nowait(np.array([0.9]))  # trigger
    await until(lambda: ctx["wakes"] == 1)
    assert cap.closed  # mic released before the exchange wants it
    await svc.stop()


async def test_disabled_service_never_opens_mic():
    svc, ctx = make_service(enabled=False)
    svc.ensure_started()
    await asyncio.sleep(0.05)
    assert ctx["captures"] == []
    await svc.stop()


async def test_suppress_closes_capture_and_resume_reopens():
    svc, ctx = make_service()
    svc.ensure_started()
    await until(lambda: ctx["captures"])
    svc.suppress()
    await until(lambda: ctx["captures"][0].closed)
    assert len(ctx["captures"]) == 1  # stays closed while suppressed
    svc.resume()
    await until(lambda: len(ctx["captures"]) == 2)  # listening again
    await svc.stop()


async def test_set_enabled_persists_and_stops_listening():
    svc, ctx = make_service()
    svc.ensure_started()
    await until(lambda: ctx["captures"])
    svc.set_enabled(False)
    await until(lambda: ctx["captures"][0].closed)
    assert ctx["persisted"] == [False]
    with pytest.raises(WakeError):
        make_service(available=False)[0].set_enabled(True)
    await svc.stop()


async def test_score_stream_below_threshold_never_wakes():
    svc, ctx = make_service()
    svc.ensure_started()
    await until(lambda: ctx["captures"])
    for s in (0.0, 0.49, 0.3, 0.1):
        ctx["captures"][0].q.put_nowait(np.array([s]))
    await until(lambda: ctx["captures"][0].q.empty())
    await asyncio.sleep(0.05)
    assert ctx["wakes"] == 0
    assert not ctx["captures"][0].closed
    await svc.stop()


# --- barge-in (handle_wake) -------------------------------------------------


def make_state(**kwargs) -> AppState:
    return AppState(
        token=TOKEN,
        store=Store(db.connect(":memory:")),
        backend=FakeBackend(chunks=("hi",)),
        config=Config(
            ollama_url="http://unused",
            default_model="",
            config_path=None,
            data_dir=None,
        ),
        **kwargs,
    )


async def test_handle_wake_cancels_generation_and_notifies():
    state = make_state()
    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    conn = Connection(send=send)
    conn.generation = asyncio.create_task(asyncio.sleep(3600))
    state.connections.append(conn)

    assert await handle_wake(state) is True
    assert conn.generation.cancelled()
    assert sent == [protocol.wake_detected()]


async def test_handle_wake_without_client_is_unhandled():
    assert await handle_wake(make_state()) is False


async def test_handle_wake_broadcasts_to_every_connection():
    """A newer connection (zombie page, diagnostic client) must not steal the
    wake: every client hears wake.detected, and a generation on ANY connection
    is barged in — not just the newest one's."""
    state = make_state()
    sent_a: list[dict] = []
    sent_b: list[dict] = []

    async def send_a(msg):
        sent_a.append(msg)

    async def send_b(msg):
        sent_b.append(msg)

    older = Connection(send=send_a)
    older.generation = asyncio.create_task(asyncio.sleep(3600))
    newer = Connection(send=send_b)
    state.connections.extend([older, newer])

    assert await handle_wake(state) is True
    assert older.generation.cancelled()
    assert sent_a == [protocol.wake_detected()]
    assert sent_b == [protocol.wake_detected()]


async def test_handle_wake_survives_a_dead_connection():
    """A connection whose send raises (half-closed socket) is skipped; the
    live one still hears the wake."""
    state = make_state()
    sent: list[dict] = []

    async def dead_send(msg):
        raise RuntimeError("socket closed")

    async def live_send(msg):
        sent.append(msg)

    state.connections.extend([Connection(send=dead_send), Connection(send=live_send)])

    assert await handle_wake(state) is True
    assert sent == [protocol.wake_detected()]


# --- over the websocket -----------------------------------------------------


class FakeWake:
    enabled = False
    available = True

    def __init__(self):
        self.calls: list = []

    def ensure_started(self) -> None:
        self.calls.append("started")

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.calls.append(("set", enabled))

    def suppress(self) -> None:
        self.calls.append("suppress")

    def resume(self) -> None:
        self.calls.append("resume")


@pytest.fixture
def make_wake_client(tmp_path):
    def _make(voice_io=None, wake=None):
        state = make_state(voice_io=voice_io)
        state.wake = wake
        from fastapi.testclient import TestClient

        return TestClient(create_app(state)), state

    return _make


def test_wake_status_sent_on_connect_and_set_roundtrip(make_wake_client):
    wake = FakeWake()
    client, _ = make_wake_client(wake=wake)
    with connect(client) as ws:
        assert ws.receive_json() == {"type": "wake.status", "enabled": False, "available": True}
        ws.send_json({"type": "wake.set", "enabled": True})
        assert ws.receive_json() == {"type": "wake.status", "enabled": True, "available": True}
    assert "started" in wake.calls and ("set", True) in wake.calls


def test_wake_set_without_service_errors(make_wake_client):
    client, _ = make_wake_client()
    with connect(client) as ws:
        ws.send_json({"type": "wake.set", "enabled": True})
        assert ws.receive_json()["code"] == "WAKE_UNAVAILABLE"


def test_voice_exchange_holds_wake_only_while_listening(make_wake_client):
    wake = FakeWake()
    client, _ = make_wake_client(voice_io=FakeVoiceIO(utterance_script()), wake=wake)
    with connect(client) as ws:
        ws.receive_json()  # wake.status
        ws.send_json({"type": "voice.start"})
        drain_voice(ws)
    assert wake.calls.count("suppress") == 1
    assert wake.calls.count("resume") == 1
    # resumed as soon as the mic closed (endpoint), not at exchange end
    assert wake.calls.index("resume") == wake.calls.index("suppress") + 1


def test_cancelled_exchange_still_releases_wake(make_wake_client):
    wake = FakeWake()
    io = FakeVoiceIO([SILENCE] * 10_000, max_wait_ms=None)
    client, _ = make_wake_client(voice_io=io, wake=wake)
    with connect(client) as ws:
        ws.receive_json()  # wake.status
        ws.send_json({"type": "voice.start"})
        while True:
            m = ws.receive_json()
            if m["type"] == "voice.state" and m["state"] == "listening":
                break
        ws.send_json({"type": "voice.stop"})
        drain_voice(ws, until_reasons=("stopped",))
    assert wake.calls.count("suppress") == 1
    assert wake.calls.count("resume") == 1


# --- persistence ------------------------------------------------------------


def test_wake_enabled_persists_across_loads():
    assert load_wake_enabled() is False
    save_wake_enabled(True)
    assert load_wake_enabled() is True
    save_wake_enabled(False)
    assert load_wake_enabled() is False


# --- the real chain (local-only: skipped when models aren't fetched) --------

# Resolved at import time, BEFORE the isolated_dirs fixture redirects
# JARVIS_DATA_DIR away from the developer's real model directory.
from jarvis_backend.assets import missing, path_for  # noqa: E402

_REAL_MODELS = {
    name: path_for(name)
    for name in ("kokoro-model", "kokoro-voices", "wake-melspec", "wake-embedding",
                 "wake-hey-jarvis")
}
_MODELS_PRESENT = not missing()


@pytest.mark.skipif(not _MODELS_PRESENT, reason="models not fetched")
def test_real_detector_hears_synthesized_hey_jarvis():
    """Kokoro speaks the wake phrase; the vendored chain must trigger on it
    and must NOT trigger on a decoy phrase. Runs only where models exist."""
    from jarvis_backend.tts.kokoro import KokoroTTS
    from jarvis_backend.wake.detector import WakeDetector

    tts = KokoroTTS(_REAL_MODELS["kokoro-model"], _REAL_MODELS["kokoro-voices"])
    det = WakeDetector(
        _REAL_MODELS["wake-melspec"],
        _REAL_MODELS["wake-embedding"],
        _REAL_MODELS["wake-hey-jarvis"],
    )

    def max_score(text: str) -> float:
        det.reset()
        audio, sr = tts.synthesize(text)
        n = int(audio.size * 16_000 / sr)
        x = np.interp(np.linspace(0, audio.size - 1, n), np.arange(audio.size), audio)
        x = np.concatenate([np.zeros(16_000), x, np.zeros(16_000)]).astype(np.float32)
        best = 0.0
        for i in range(0, x.size - 1280, 1280):
            score = det.feed(x[i : i + 1280])
            if score is not None:
                best = max(best, score)
        return best

    assert max_score("Hey Jarvis!") > 0.9
    assert max_score("Hello there, how are you?") < 0.2
