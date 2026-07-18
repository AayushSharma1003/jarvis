"""Speaker playback: a pull-model output stream fed by TTS chunks.

The PortAudio callback drains an internal buffer; enqueue() appends. stop()
clears the buffer instantly — that's the barge-in path, so it must never wait
for queued audio. The stream itself stays open (reopen costs ~100 ms and an
audible pop) and plays zeros when idle.

`level` is a smoothed 0–1 loudness of what's currently playing, read by the
voice session (~10 Hz) and forwarded to the UI — the sphere's speaking state
feeds on it in Phase 3.
"""

from __future__ import annotations

import asyncio
import threading

import numpy as np

from .devices import AudioError

BLOCK = 1024
_LEVEL_GAIN = 4.0  # speech RMS ≈ 0.05–0.25 → map into 0–1
_SMOOTH = 0.6


class Player:
    def __init__(self, samplerate: int = 24_000):
        self.samplerate = samplerate
        self.level = 0.0
        self._lock = threading.Lock()
        self._buffer: list[np.ndarray] = []
        self._offset = 0  # into _buffer[0]
        self._pending = 0  # samples not yet handed to the device
        self._stream = None

    def start(self) -> None:
        if self._stream is not None:
            return
        try:
            import sounddevice as sd
        except (ImportError, OSError) as e:
            raise AudioError("AUDIO_RUNTIME_MISSING", str(e)) from e
        try:
            self._stream = sd.OutputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="float32",
                blocksize=BLOCK,
                callback=self._callback,
            )
            self._stream.start()
        except sd.PortAudioError as e:
            raise AudioError("SPEAKER_UNAVAILABLE", str(e)) from e

    def _callback(self, outdata, frames, time_info, status) -> None:
        filled = 0
        with self._lock:
            while filled < frames and self._buffer:
                head = self._buffer[0]
                take = min(frames - filled, len(head) - self._offset)
                outdata[filled : filled + take, 0] = head[self._offset : self._offset + take]
                self._offset += take
                filled += take
                self._pending -= take
                if self._offset >= len(head):
                    self._buffer.pop(0)
                    self._offset = 0
        if filled < frames:
            outdata[filled:, 0] = 0.0
        block = outdata[:, 0]
        rms = float(np.sqrt(np.mean(block * block)))
        self.level = _SMOOTH * self.level + (1 - _SMOOTH) * min(1.0, rms * _LEVEL_GAIN)

    def enqueue(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        with self._lock:
            self._buffer.append(samples.astype(np.float32, copy=False))
            self._pending += len(samples)

    @property
    def pending(self) -> int:
        with self._lock:
            return self._pending

    def stop(self) -> None:
        """Barge-in: silence immediately, keep the stream alive."""
        with self._lock:
            self._buffer.clear()
            self._offset = 0
            self._pending = 0
        self.level = 0.0

    async def drain(self) -> None:
        """Wait until everything enqueued has been played out."""
        while self.pending > 0:
            await asyncio.sleep(0.05)
        # Let the device's last buffered block leave the speaker.
        await asyncio.sleep(2 * BLOCK / self.samplerate)

    def close(self) -> None:
        self.stop()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
