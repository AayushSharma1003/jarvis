"""The voice exchange: push-to-talk → VAD endpoint → STT → LLM → chunked TTS.

VoiceIO is the hardware/model boundary — the real implementation owns the mic,
speaker, and the three engines; tests inject a fake and drive the whole
orchestration over the WebSocket with zero hardware or model files.

One voice exchange is one generation task (same slot as a text chat), so BUSY
semantics, chat.stop, and disconnect cleanup all behave identically. The LLM
leg reuses run_exchange: spoken turns persist exactly like typed ones.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol

import numpy as np

from ..agent.loop import run_exchange
from ..audio.devices import AudioError
from ..llm.tiering import pick_model
from ..stt.endpointing import Endpointer, Event, State
from ..stt.transcriber import STTError
from ..tts.base import TTSError
from ..tts.chunker import SentenceChunker
from . import protocol

LEVEL_INTERVAL_S = 0.1  # 10 Hz UI level updates (sphere food)
_LISTEN_LEVEL_GAIN = 6.0


class VoiceUnavailable(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class Capture(Protocol):
    def start(self) -> None: ...
    def backlog(self) -> list[np.ndarray]: ...  # audio buffered before we started reading
    def chunks(self) -> AsyncIterator[np.ndarray]: ...
    def close(self) -> None: ...


class Playback(Protocol):
    samplerate: int
    level: float

    def start(self) -> None: ...
    def enqueue(self, samples: np.ndarray) -> None: ...
    @property
    def pending(self) -> int: ...
    def stop(self) -> None: ...
    async def drain(self) -> None: ...


class VoiceIO(Protocol):
    """Everything the session needs from hardware + models."""

    def load(self) -> None:
        """Blocking, idempotent heavy load (called via to_thread once)."""
        ...

    def open_capture(self) -> Capture: ...
    def player(self) -> Playback: ...
    def vad_prob(self, chunk: np.ndarray) -> float: ...
    def transcribe(self, audio: np.ndarray) -> str: ...  # blocking
    def synthesize(self, text: str) -> tuple[np.ndarray, int]: ...  # blocking
    def make_endpointer(self) -> Endpointer: ...


class RealVoiceIO:
    """Lazy-loads Silero + whisper + Kokoro on first use; owns mic/speaker."""

    def __init__(self) -> None:
        self._loaded = False
        self._tts: Any = None
        self._player: Any = None

    def load(self) -> None:
        """Load what *listening* needs. Blocking, idempotent.

        Kokoro is deliberately not loaded here. Its onnxruntime session setup
        plus first synthesis takes ~2.2 s and saturates every core, and that
        starves the CoreAudio callback thread badly enough to lose two thirds
        of the microphone input — measured: 33-38% of chunks delivered while
        it runs, with no PortAudio overflow flag to warn you. Since the mic is
        open from the start of the exchange (so the opening words survive),
        that lost audio is exactly what the user is saying. TTS loads on the
        first synthesize() instead, once the mic is closed and the CPU is ours.
        Whisper and Silero are fine to load here: measured at ~100% and no
        louder than one utterance of decoding.
        """
        if self._loaded:
            return
        from ..assets import path_for
        from ..stt.transcriber import Transcriber
        from ..stt.vad import SileroVAD

        # Fail on a missing voice *now* rather than three states later, without
        # paying for the session: the old load() surfaced this at the same point.
        for asset in ("kokoro-model", "kokoro-voices"):
            if not path_for(asset).is_file():
                raise TTSError("TTS_MODEL_MISSING", str(path_for(asset)))
        self._vad = SileroVAD(path_for("silero-vad"))
        self._stt = Transcriber(path_for("whisper-base"))
        # First whisper run compiles Metal shaders — do it here, not mid-utterance.
        self._stt.transcribe(np.zeros(16_000, dtype=np.float32))
        self._loaded = True

    def _ensure_tts(self) -> Any:
        if self._tts is None:
            from ..assets import path_for
            from ..tts.kokoro import KokoroTTS

            tts = KokoroTTS(path_for("kokoro-model"), path_for("kokoro-voices"))
            tts.synthesize("Ready.")  # onnxruntime graph setup, off the mic's back
            self._tts = tts
        return self._tts

    def open_capture(self) -> Capture:
        from ..audio.capture import MicCapture

        cap = MicCapture()
        cap.start()
        return cap

    def player(self) -> Playback:
        if self._player is None:
            from ..audio.playback import Player

            self._player = Player(samplerate=24_000)
        return self._player

    def vad_prob(self, chunk: np.ndarray) -> float:
        return self._vad.prob(chunk)

    def transcribe(self, audio: np.ndarray) -> str:
        return self._stt.transcribe(audio)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        return self._ensure_tts().synthesize(text)

    def make_endpointer(self) -> Endpointer:
        return Endpointer()


async def run_voice_exchange(state, send, msg: dict[str, Any]) -> None:
    """The whole voice turn. Runs as the connection's generation task."""
    io: VoiceIO | None = state.voice_io
    if io is None:
        await send(protocol.error("VOICE_UNAVAILABLE"))
        return

    player: Playback | None = None
    capture: Capture | None = None
    level_task: asyncio.Task | None = None
    # The wake service pauses only while WE own the mic ("hey jarvis" mid-
    # utterance must not re-trigger); it resumes for thinking/speaking so the
    # wake word can barge in on playback.
    wake = state.wake
    wake_held = False

    def _release_wake() -> None:
        nonlocal wake_held
        if wake_held:
            wake.resume()
            wake_held = False

    try:
        # The mic opens BEFORE the engines load. The first exchange after app
        # start pays ~0.45 s in io.load() (whisper's Metal shaders) and people
        # start talking the instant they trigger a turn, so loading first meant
        # the opening words were never captured at all. MicCapture's queue
        # holds that window; backlog() collects it below. See RealVoiceIO.load
        # for why TTS is not part of it.
        await send(protocol.voice_state("loading"))
        if wake is not None:
            wake.suppress()
            wake_held = True
        try:
            capture = io.open_capture()
        except AudioError as e:
            await send(protocol.error(e.code, e.detail))
            await send(protocol.voice_state("idle", reason="error"))
            return
        try:
            await asyncio.to_thread(io.load)
            player = io.player()
            player.start()
        except (STTError, TTSError, AudioError) as e:
            await send(protocol.error(e.code, e.detail))
            await send(protocol.voice_state("idle", reason="error"))
            return
        except Exception as e:  # VADError shares no base; keep codes machine-readable
            code = getattr(e, "code", "VOICE_LOAD_FAILED")
            await send(protocol.error(code, getattr(e, "detail", str(e))))
            await send(protocol.voice_state("idle", reason="error"))
            return

        # ---- listen ----
        await send(protocol.voice_state("listening"))
        endpointer = io.make_endpointer()
        utterance = None
        last_level = 0.0
        # Replay the load window first. No level updates for it: it is history,
        # and the sphere would get a burst of stale RMS in a single tick.
        backlog = capture.backlog()
        for chunk in backlog:
            if endpointer.feed(chunk, io.vad_prob(chunk)) == Event.SPEECH_END:
                utterance = endpointer.utterance()
                break
        if utterance is None and endpointer.state is not State.SPEECH:
            # Only room tone while the engines loaded (or a no-speech timeout
            # on it). Start the wait from when the user could see "listening"
            # rather than spending the budget on audio nobody was prompted for.
            endpointer.reset()
        if utterance is None:  # the whole utterance can land inside the load window
            async for chunk in capture.chunks():
                event = endpointer.feed(chunk, io.vad_prob(chunk))
                now = time.monotonic()
                if now - last_level >= LEVEL_INTERVAL_S:
                    last_level = now
                    rms = float(np.sqrt(np.mean(chunk * chunk)))
                    await send(protocol.voice_level(min(1.0, rms * _LISTEN_LEVEL_GAIN)))
                if event == Event.TIMEOUT:
                    await send(protocol.voice_state("idle", reason="no_speech"))
                    return
                if event == Event.SPEECH_END:
                    utterance = endpointer.utterance()
                    break
        capture.close()
        capture = None
        _release_wake()
        if utterance is None or utterance.size == 0:
            await send(protocol.voice_state("idle", reason="no_speech"))
            return

        # ---- transcribe ----
        await send(protocol.voice_state("transcribing"))
        text = await asyncio.to_thread(io.transcribe, utterance)
        if not text:
            await send(protocol.voice_state("idle", reason="no_speech"))
            return
        await send(protocol.stt_text(text))

        # ---- think + speak ----
        conversation_id = msg.get("conversation_id") or state.store.create_conversation(
            title=text[:80]
        )
        model = msg.get("model") or pick_model(
            await state.backend.list_models(), state.config.default_model
        )
        await send(protocol.chat_start(conversation_id, model))
        await send(protocol.voice_state("thinking"))

        chunker = SentenceChunker()
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        speaking = asyncio.Event()
        synth_task = asyncio.create_task(_synth_worker(io, player, sentences, send, speaking))
        level_task = asyncio.create_task(_level_reporter(player, send, speaking))

        async def on_delta(delta: str) -> None:
            await send(protocol.chat_delta(delta))
            for sentence in chunker.feed(delta):
                sentences.put_nowait(sentence)

        try:
            result = await run_exchange(
                store=state.store,
                backend=state.backend,
                model=model,
                conversation_id=conversation_id,
                user_text=text,
                on_delta=on_delta,
                parent_turn_id=msg.get("parent_turn_id"),
                voice_mode=True,
            )
        finally:
            if rest := chunker.flush():
                sentences.put_nowait(rest)
            sentences.put_nowait(None)

        if result.error_code:
            await send(protocol.error(result.error_code, result.error_detail))
        if result.turn_id is not None:
            await send(protocol.chat_done(conversation_id, result.turn_id, result.interrupted))

        await synth_task
        await player.drain()
        level_task.cancel()
        await send(protocol.voice_state("idle", reason="done"))

    except asyncio.CancelledError:
        # voice.stop / chat.stop / disconnect: silence NOW (barge-in path).
        if player is not None:
            player.stop()
        with contextlib.suppress(Exception):
            await send(protocol.voice_state("idle", reason="stopped"))
        raise
    finally:
        if level_task is not None and not level_task.done():
            level_task.cancel()
        if capture is not None:
            capture.close()
        _release_wake()


async def _synth_worker(
    io: VoiceIO,
    player: Playback,
    sentences: asyncio.Queue[str | None],
    send,
    speaking: asyncio.Event,
) -> None:
    """Synthesize sentences strictly in order; the player buffers the audio."""
    while (text := await sentences.get()) is not None:
        try:
            samples, _sr = await asyncio.to_thread(io.synthesize, text)
        except TTSError as e:
            await send(protocol.error(e.code, e.detail))
            continue  # keep speaking the rest; one bad sentence isn't fatal
        if samples.size:
            player.enqueue(samples)
            if not speaking.is_set():
                speaking.set()
                await send(protocol.voice_state("speaking"))


async def _level_reporter(player: Playback, send, speaking: asyncio.Event) -> None:
    await speaking.wait()
    with contextlib.suppress(asyncio.CancelledError):
        while True:
            await send(protocol.voice_level(player.level))
            await asyncio.sleep(LEVEL_INTERVAL_S)
