"""Microphone capture: 16 kHz mono float32 in VAD-sized chunks.

The PortAudio callback runs on a native thread; chunks cross into asyncio via
call_soon_threadsafe onto a bounded queue. If the consumer stalls, we drop the
oldest audio rather than block the audio thread (a glitch beats a deadlock).

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


class MicCapture:
    """One capture stream. start() → chunks() → close(). Not reusable."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=QUEUE_CHUNKS)
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

    async def chunks(self) -> AsyncIterator[np.ndarray]:
        while True:
            yield await self._queue.get()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
