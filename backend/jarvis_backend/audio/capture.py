"""Microphone capture: 16 kHz mono float32 in VAD-sized chunks.

The PortAudio callback runs on a native thread; chunks cross into asyncio via
call_soon_threadsafe onto a bounded queue. If the consumer stalls, we drop the
oldest audio rather than block the audio thread (a glitch beats a deadlock).

MicCapture's queue is deliberately deep. A voice exchange opens the mic
*before* loading the engines so the opening words are never lost, and nothing
consumes the stream until the load finishes — so the queue is the pre-roll
buffer for that window. `backlog()` drains it in one go before live iteration
begins. The wake worker's SyncMicCapture keeps the small queue: it drains
continuously, and if it ever stalls, dropping old audio is what we want.

macOS note: the first stream open triggers the system microphone-permission
prompt (attributed to the app bundle in production, the terminal in dev). A
denied permission yields *silence*, not an error — the endpointer's no-speech
timeout is what surfaces that to the user.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import numpy as np

from ..stt.vad import CHUNK_SAMPLES, SAMPLE_RATE
from .devices import AudioError

QUEUE_CHUNKS = 64  # ~2 s of buffered audio before we start dropping
# Deep enough to cover a cold engine load (~2.5 s) with room to spare, so no
# word spoken during it is dropped. 8 s ≈ 512 KB.
PREBUFFER_CHUNKS = 8 * SAMPLE_RATE // CHUNK_SAMPLES


class MicCapture:
    """One capture stream. start() → backlog() → chunks() → close()."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=PREBUFFER_CHUNKS)
        self._loop = asyncio.get_running_loop()
        self._stream = None

    def start(self) -> None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as e:
            raise AudioError("AUDIO_RUNTIME_MISSING", str(e)) from e

        def callback(indata, frames, time_info, status) -> None:
            chunk = np.ascontiguousarray(indata[:, 0], dtype=np.float32).copy()
            self._loop.call_soon_threadsafe(self._offer, chunk)

        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=CHUNK_SAMPLES,
                callback=callback,
            )
            self._stream.start()
        except sd.PortAudioError as e:
            raise AudioError("MIC_UNAVAILABLE", str(e)) from e

    def _offer(self, chunk: np.ndarray) -> None:
        if self._queue.full():
            self._queue.get_nowait()  # drop oldest
        self._queue.put_nowait(chunk)

    def backlog(self) -> list[np.ndarray]:
        """Everything captured but not yet consumed, oldest first.

        Call once before iterating chunks(): it is the audio spoken while the
        engines were still loading. Draining it separately (rather than letting
        chunks() serve it) gives the caller the boundary between "history" and
        "live", which the endpointer needs — see run_voice_exchange.
        """
        out: list[np.ndarray] = []
        while not self._queue.empty():
            out.append(self._queue.get_nowait())
        return out

    async def chunks(self) -> AsyncIterator[np.ndarray]:
        while True:
            yield await self._queue.get()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class SyncMicCapture:
    """Blocking-queue capture for the wake worker thread — no asyncio in the
    path, so an always-on consumer costs no event-loop wakeups. Larger
    blocksize than MicCapture: fewer PortAudio callbacks matters when the
    stream runs for hours, and wake detection doesn't need 32 ms granularity.
    """

    def __init__(self, blocksize: int = 1024) -> None:
        import queue

        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=QUEUE_CHUNKS)
        self._blocksize = blocksize
        self._stream = None

    def start(self) -> None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as e:
            raise AudioError("AUDIO_RUNTIME_MISSING", str(e)) from e

        def callback(indata, frames, time_info, status) -> None:
            chunk = np.ascontiguousarray(indata[:, 0], dtype=np.float32).copy()
            if self._queue.full():
                try:
                    self._queue.get_nowait()  # drop oldest
                except Exception:
                    pass
            self._queue.put_nowait(chunk)

        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=self._blocksize,
                callback=callback,
            )
            self._stream.start()
        except sd.PortAudioError as e:
            raise AudioError("MIC_UNAVAILABLE", str(e)) from e

    def get(self, timeout: float) -> np.ndarray:
        """Blocking read; raises queue.Empty on timeout."""
        return self._queue.get(timeout=timeout)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
