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
