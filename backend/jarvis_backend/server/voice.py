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
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol

import numpy as np

from ..agent.loop import run_exchange
from ..audio.devices import AudioError
from ..audio.silence import SilenceWatch
from ..llm.tiering import pick_model
from ..stt.endpointing import Endpointer, Event, State
from ..stt.transcriber import STTError
from ..tts.base import TTSError
from ..tts.chunker import SentenceChunker
from ..wake.service import contains_wake_word
from . import protocol

log = logging.getLogger(__name__)

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
        """Construct only — the caller starts it off the event loop.

        Starting here would put Pa_OpenStream on the loop, and a pending
        microphone permission prompt would then freeze the entire backend.
        MicCapture's constructor needs the running loop, so the split is
        deliberate rather than cosmetic: build here, `start()` in a thread.
        """
        from ..audio.capture import MicCapture

        return MicCapture()

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


# `speak_line` shares RealVoiceIO's single cached Player with everything else
# that speaks, so two announcements arriving together would splice their samples
# into one stream. One at a time, in arrival order.
_SPEAK_LOCK = asyncio.Lock()


async def speak_line(state, text: str) -> None:
    """Say one sentence outside a voice exchange (M5.4). Never raises.

    An extension's timer fires when it fires — almost never during a spoken
    turn — so `voice.say` needed somewhere to go when there is no sentence
    queue to push into. The wording is still the frontend's: it renders the
    sentence from a notification's `code` + `data` and hands it back, exactly
    as it does for the confirm prompt (§ the i18n rule in CLAUDE.md).

    **A live turn always wins**, and the check is across every connection, not
    just the one that sent the message. The queue belongs to the *turn*, not to
    the connection that happened to receive the broadcast, so a zombie window
    answering must not synthesize behind the back of the window that is
    mid-exchange: two voices would interleave in the shared player, and while
    the mic is still open the synthesis would starve the capture (gotcha 11).

    Failure is silence. A missing Kokoro, an unavailable speaker or a text-only
    build all mean the user gets the toast without the announcement — the same
    degradation as the readiness gate, where missing voice models warn rather
    than block.
    """
    for conn in list(getattr(state, "connections", [])):
        queue = getattr(conn, "voice_sentences", None)
        if queue is not None:
            queue.put_nowait(text)
            return

    io = state.voice_io
    if io is None:
        return
    # Same self-wake hazard as the exchange, and worse here: nothing has
    # suppressed the wake service, because no turn is in flight. A frontend
    # sentence is free to contain "Jarvis" ("Jarvis here — your timer's up"),
    # and hearing itself say that would open a voice turn nobody asked for.
    wake = getattr(state, "wake", None)
    held = wake is not None and contains_wake_word(text)
    async with _SPEAK_LOCK:
        if held:
            wake.suppress()
        try:
            player = io.player()
            player.start()
            samples, _sr = await asyncio.to_thread(io.synthesize, text)
            if samples.size:
                player.enqueue(samples)
                await player.drain()
        except Exception:  # noqa: BLE001 - an announcement is never worth a crash
            log.warning("could not speak a notification", exc_info=True)
        finally:
            # In the finally, not after the try: a failed synthesis must still
            # give the wake word back, or one broken announcement kills
            # always-on listening until the app restarts.
            if held:
                wake.resume()


async def run_voice_exchange(state, send, msg: dict[str, Any], conn=None) -> None:
    """The whole voice turn. Runs as the connection's generation task.

    `conn` is threaded through for exactly one reason: to publish the sentence
    queue, so `voice.say` can have the backend speak a line the *frontend*
    wrote (the confirmation prompt — the backend must not author English). The
    conversation id deliberately still travels the other way, sniffed out of
    chat.start by `_generation_send`, because it doesn't exist yet when the
    task starts.
    """
    io: VoiceIO | None = state.voice_io
    if io is None:
        await send(protocol.error("VOICE_UNAVAILABLE"))
        return

    player: Playback | None = None
    capture: Capture | None = None
    level_task: asyncio.Task | None = None
    synth_task: asyncio.Task | None = None
    # The wake service pauses only while WE own the mic ("hey jarvis" mid-
    # utterance must not re-trigger); it resumes for thinking/speaking so the
    # wake word can barge in on playback.
    wake = state.wake
    wake_held = False
    # ...with one exception: a reply that says the wake word wakes US. No AEC
    # in v1, so the mic hears the speakers, and "I'm JARVIS, your…" scores
    # 0.990 against a 0.5 threshold on the real chain. This is a SECOND,
    # independent hold — the listening hold is already released by the time
    # anything is spoken, so one flag could not track both.
    self_speech_held = False

    def _release_wake() -> None:
        nonlocal wake_held
        if wake_held:
            wake.resume()
            wake_held = False

    def _release_self_speech() -> None:
        nonlocal self_speech_held
        if self_speech_held:
            wake.resume()
            self_speech_held = False

    def _guard_self_speech(text: str) -> None:
        """Silence wake for the rest of this turn if we are about to say our
        own name. Once per turn: the counter is reentrant, but resume() is
        driven by a single flag, and re-suppressing per sentence would need a
        matching count to unwind."""
        nonlocal self_speech_held
        if wake is None or self_speech_held or not contains_wake_word(text):
            return
        wake.suppress()
        self_speech_held = True

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
            # Construct on the loop (MicCapture needs the running loop for its
            # threadsafe hand-off), but START off it: start() is Pa_OpenStream,
            # which blocks while macOS decides whether we may record — forever,
            # if the permission prompt has not been answered. On the loop that
            # freezes the whole backend, not just voice: no /health, no chat,
            # no UI. See gotcha 38.
            capture = io.open_capture()
            await asyncio.to_thread(capture.start)
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
        # Watches for a microphone that is delivering nothing at all, so the
        # timeout below can say "I cannot hear you" instead of "say it again".
        # See audio/silence.py and gotcha 36.
        silence = SilenceWatch()
        # Replay the load window first. No level updates for it: it is history,
        # and the sphere would get a burst of stale RMS in a single tick.
        backlog = capture.backlog()
        for chunk in backlog:
            silence.feed(chunk)
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
                silence.feed(chunk)
                event = endpointer.feed(chunk, io.vad_prob(chunk))
                now = time.monotonic()
                if now - last_level >= LEVEL_INTERVAL_S:
                    last_level = now
                    rms = float(np.sqrt(np.mean(chunk * chunk)))
                    await send(protocol.voice_level(min(1.0, rms * _LISTEN_LEVEL_GAIN)))
                if event == Event.TIMEOUT:
                    # A window that heard literally nothing is a broken mic, not
                    # a quiet user — telling them to try again cannot help.
                    reason = "mic_silent" if silence.is_dead else "no_speech"
                    await send(protocol.voice_state("idle", reason=reason))
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
        # Reachable from `voice.say` for as long as this turn is speaking.
        # _synth_worker consumes strictly in order, so a prompt pushed here
        # lands behind whatever the model has already said — acceptable,
        # because at a tool call it has usually said very little.
        if conn is not None:
            conn.voice_sentences = sentences
        speaking = asyncio.Event()
        synth_task = asyncio.create_task(
            _synth_worker(io, player, sentences, send, speaking, _guard_self_speech)
        )
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
                parent_turn_id=protocol.parent_turn_from(msg),
                voice_mode=True,
                registry=await state.registry_for(model),
                on_span=lambda span: send(protocol.tool_span(span)),
                taint=state.taint,
            )
        finally:
            if rest := chunker.flush():
                sentences.put_nowait(rest)
            sentences.put_nowait(None)

        if result.error_code:
            await send(protocol.error(result.error_code, result.error_detail))
        if result.turn_id is not None:
            await send(protocol.chat_done(conversation_id, result.turn_id, result.interrupted))

        # **Barge-in during generation lands here, not in `except CancelledError`.**
        # run_exchange deliberately absorbs CancelledError so it can still persist
        # the partial turn (agent/loop.py; the delete-races-the-generation guard
        # depends on that). The side effect is that a `voice.stop`, a `chat.stop`
        # or a wake-word barge-in raised *while the model is still streaming*
        # returns here as an ordinary result — and we would go on to await the
        # synth worker and `player.drain()`, i.e. speak the whole queued reply to
        # someone who just interrupted it, and hold the generation slot (and
        # `handle_wake`'s `await cancel_generation()`) for the length of it.
        #
        # `cancelling()` is the exact question — "was this task cancelled?" —
        # and survives the absorbed CancelledError. Re-raising keeps ONE
        # barge-in path: the handler below silences the player and reports
        # `stopped`, and the canceller still sees a cancelled task. It goes
        # AFTER chat.done on purpose: the frontend clears `streamKey` on that
        # message, and skipping it strands the composer forever.
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError

        await synth_task
        await player.drain()
        level_task.cancel()
        await send(protocol.voice_state("idle", reason="done"))

    except asyncio.CancelledError:
        # voice.stop / chat.stop / disconnect: silence NOW (barge-in path).
        # Kill the synth worker FIRST. It is a separate task, so our cancellation
        # does not reach it unless we happen to be awaiting it; parked inside
        # `to_thread(io.synthesize, ...)` it would finish that sentence and
        # `enqueue()` it — and Player.stop() only *clears* the buffer, leaving the
        # stream open (audio/playback.py), so the late chunk refills it and
        # Jarvis speaks one more sentence after being told to stop. Cancelling
        # before the `await` below means the worker can never reach its enqueue.
        if synth_task is not None:
            synth_task.cancel()
        if player is not None:
            player.stop()
        with contextlib.suppress(Exception):
            await send(protocol.voice_state("idle", reason="stopped"))
        raise
    except Exception:  # noqa: BLE001
        # Same reasoning as _generate's catch-all, plus one more: this task also
        # owns the voice state machine, so dying silently strands the UI in
        # "thinking" with the sphere spinning and the mic button showing stop.
        if player is not None:
            player.stop()
        with contextlib.suppress(Exception):
            await send(protocol.error("GENERATION_FAILED"))
            await send(protocol.voice_state("idle", reason="error"))
    finally:
        if level_task is not None and not level_task.done():
            level_task.cancel()
        # Same reasoning as the cancellation handler, for the `except Exception`
        # route: never leave a synth worker alive to enqueue into a turn that
        # has ended.
        if synth_task is not None and not synth_task.done():
            synth_task.cancel()
        if capture is not None:
            capture.close()
        if conn is not None:
            conn.voice_sentences = None
        _release_wake()
        _release_self_speech()


async def _synth_worker(
    io: VoiceIO,
    player: Playback,
    sentences: asyncio.Queue[str | None],
    send,
    speaking: asyncio.Event,
    guard_self_speech=None,
) -> None:
    """Synthesize sentences strictly in order; the player buffers the audio.

    `guard_self_speech` is consulted BEFORE synthesis, not before enqueue: the
    wake service must already be down by the time any of this audio can reach
    a speaker, and synthesis is the slow part.
    """
    while (text := await sentences.get()) is not None:
        if guard_self_speech is not None:
            guard_self_speech(text)
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
