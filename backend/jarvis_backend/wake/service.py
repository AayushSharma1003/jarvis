"""The always-on wake-word service: owns a mic stream while enabled.

Lifecycle: one background asyncio task per process, but the audio work runs
in a single long-lived worker thread (one asyncio.to_thread call per listen
session) — per-chunk event-loop crossings at 30 Hz measurably cost several
percent of a core, and always-on means always. The thread reads a blocking
queue fed by the PortAudio callback and runs the VAD-gated pipeline
(wake/pipeline.py); the asyncio side only hears about triggers.

The service listens whenever the user has the toggle on AND nothing
suppresses it. The voice exchange suppresses it for the *listening* phase
only (one mic pipeline at a time, and "hey jarvis" mid-utterance must not
re-trigger); it runs again during thinking/speaking so the wake word can
barge in — on_wake cancels the active generation, which stops playback
instantly.

**With one exception, and it is not theoretical: Jarvis saying its own name.**
There is no AEC in v1, so the microphone hears the speakers. Ask the app to
introduce itself and it replies "I'm JARVIS, your…", the detector hears that
through the room, and it barges in on itself — measured on the real chain at
score **0.990** against a 0.5 threshold, three times in one sentence, where
"The capital of France is Paris." scores 0.000. The user sees two words spoken
and then, abruptly, listening. So the voice exchange takes a *second*
suppression for any sentence whose text contains the wake word
(`contains_wake_word`), held to the end of that turn's speech. Barge-in stays
live for every other reply, which is the point — suppressing the whole
speaking phase would have been three lines and would have cut an approved v1
feature.

The enable toggle persists (config.save_wake_enabled) so it survives
restarts: on stays on until the user turns it off. The mic is only ever open
while the toggle is on — flipping it off closes the stream within one chunk
(~64 ms).

Everything hardware/model-shaped is injected (pipeline/capture factories,
the on_wake callback), so tests drive the full service with fakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import queue
import threading
from collections.abc import Awaitable, Callable

from ..audio.devices import AudioError
from .detector import WakeError

COOLDOWN_S = 1.0  # after a trigger: debounce + time for the client to act
_ERROR_RETRY_S = 5.0  # mic/model failure: don't hot-loop, retry calmly
_POLL_S = 0.2  # worker's queue timeout = how fast it notices stop requests

# The words that can trigger the shipped detector, for the self-speech check.
#
# Only "jarvis", and the omission is the decision: "Hey Friday" is cut from v1
# (architecture.md) so no model can fire on it, while `friday` is a common
# English word. Listing it would silence barge-in on "your meeting is on
# Friday" to protect a wake word that does not exist. Add it here in the same
# commit that ships the model, never before.
#
# Substring rather than word-boundary matching, deliberately: the detector is
# acoustic and does not care about punctuation or morphology, so "Jarvis's"
# and "JARVIS," must both count. A false positive costs one reply's barge-in;
# a false negative costs the bug this exists to stop.
_WAKE_WORDS = ("jarvis",)


def contains_wake_word(text: str) -> bool:
    """Could speaking this text aloud trigger our own detector?

    Used by the voice exchange to silence wake for the rest of a turn whose
    reply says the wake word — see the module docstring. Generalises past the
    reported bug on purpose: it is equally true of Jarvis reading a file or a
    web page that happens to contain the word.
    """
    folded = text.casefold()
    return any(w in folded for w in _WAKE_WORDS)


class WakeService:
    def __init__(
        self,
        *,
        make_pipeline: Callable[[], object],  # wake.pipeline.WakePipeline shape
        open_capture: Callable[[], object],  # audio.capture.SyncMicCapture shape
        on_wake: Callable[[], Awaitable[bool]],
        persist: Callable[[bool], None],
        enabled: bool,
        threshold: float,
        available: bool,
    ) -> None:
        self._make_pipeline = make_pipeline
        self._open_capture = open_capture
        self._on_wake = on_wake
        self._persist = persist
        self._threshold = threshold
        self.available = available
        self.enabled = enabled and available
        self._suppressed = 0
        self._wakeup = asyncio.Event()  # pokes the async loop on state changes
        self._stop_worker = threading.Event()  # pokes the worker thread
        self._task: asyncio.Task | None = None
        self._pipeline = None

    # -- state -------------------------------------------------------------

    @property
    def _should_listen(self) -> bool:
        return self.enabled and self.available and self._suppressed == 0

    def _poke(self) -> None:
        self._stop_worker.set()
        self._wakeup.set()

    def set_enabled(self, enabled: bool) -> None:
        """Flip the persistent toggle. Raises WakeError if unavailable."""
        if enabled and not self.available:
            raise WakeError("WAKE_UNAVAILABLE", "wake models missing")
        self.enabled = enabled
        self._persist(enabled)
        self._poke()

    def suppress(self) -> None:
        """Pause listening (reentrant). The voice exchange holds this only
        while it owns the mic."""
        self._suppressed += 1
        self._poke()

    def resume(self) -> None:
        self._suppressed = max(0, self._suppressed - 1)
        self._wakeup.set()

    # -- the loop ----------------------------------------------------------

    def ensure_started(self) -> None:
        """Idempotent; needs a running event loop (called from the server)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            if not self._should_listen:
                self._wakeup.clear()
                if not self._should_listen:  # re-check: no lost wakeups
                    await self._wakeup.wait()
                continue
            stop = threading.Event()
            self._stop_worker = stop
            try:
                triggered = await asyncio.to_thread(self._listen_blocking, stop)
            except (AudioError, WakeError):
                # Mic vanished or a model failed mid-stream. The toggle stays
                # on (a USB mic may come back); just don't spin.
                await asyncio.sleep(_ERROR_RETRY_S)
                continue
            finally:
                stop.set()  # if WE were cancelled, the thread must exit too
            if triggered:
                handled = False
                with contextlib.suppress(Exception):
                    handled = await self._on_wake()
                if handled:
                    await asyncio.sleep(COOLDOWN_S)

    def _listen_blocking(self, stop: threading.Event) -> bool:
        """One capture session, run in the worker thread: open mic, run the
        gated pipeline until a trigger (True) or a stop request (False).
        The capture is closed HERE, before returning — so a trigger hands
        the voice exchange a free mic."""
        if self._pipeline is None:
            self._pipeline = self._make_pipeline()
        pipeline = self._pipeline
        pipeline.reset()
        capture = self._open_capture()
        try:
            while not stop.is_set():
                try:
                    chunk = capture.get(timeout=_POLL_S)
                except queue.Empty:
                    continue
                score = pipeline.process(chunk)
                if score is not None and score >= self._threshold:
                    return True
            return False
        finally:
            capture.close()
