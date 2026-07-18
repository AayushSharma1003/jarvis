"""Utterance endpointing: turns per-chunk VAD probabilities into speech events.

Pure state machine — no VAD, no audio I/O — so it unit-tests with synthetic
probabilities and CI never needs model files. The voice session couples it
with SileroVAD: one prob() per captured chunk, fed here.

Timeline of one utterance:

    [pre-roll ring] ... speech ≥ min_speech → SPEECH_START
    ... prob < end_prob for ≥ min_silence → SPEECH_END (utterance() ready)

Hysteresis: a higher threshold starts speech than sustains it, so trailing
soft syllables don't get chopped. The pre-roll ring keeps audio from just
before detection so plosive onsets ("t", "p") survive.
"""

from __future__ import annotations

from collections import deque
from enum import Enum, auto

import numpy as np

from .vad import CHUNK_SAMPLES, SAMPLE_RATE

CHUNK_MS = CHUNK_SAMPLES * 1000 // SAMPLE_RATE  # 32 ms


class Event(Enum):
    NONE = auto()
    SPEECH_START = auto()
    SPEECH_END = auto()  # utterance() is ready
    TIMEOUT = auto()  # no speech began within max_wait_ms


class State(Enum):
    IDLE = auto()
    SPEECH = auto()
    DONE = auto()


def _to_chunks(ms: int) -> int:
    return max(1, ms // CHUNK_MS)


class Endpointer:
    def __init__(
        self,
        start_prob: float = 0.5,
        end_prob: float = 0.35,
        min_speech_ms: int = 96,
        min_silence_ms: int = 700,
        pre_roll_ms: int = 320,
        max_utterance_ms: int = 30_000,
        max_wait_ms: int | None = 10_000,
    ):
        self._start_prob = start_prob
        self._end_prob = end_prob
        self._min_speech = _to_chunks(min_speech_ms)
        self._min_silence = _to_chunks(min_silence_ms)
        self._pre_roll = _to_chunks(pre_roll_ms)
        self._max_utterance = _to_chunks(max_utterance_ms)
        self._max_wait = None if max_wait_ms is None else _to_chunks(max_wait_ms)
        self.reset()

    def reset(self) -> None:
        self.state = State.IDLE
        # Ring holds pre-roll plus the not-yet-confirmed speech run.
        self._ring: deque[np.ndarray] = deque(maxlen=self._pre_roll + self._min_speech)
        self._speech_chunks: list[np.ndarray] = []
        self._speech_run = 0
        self._silence_run = 0
        self._idle_chunks = 0

    def feed(self, chunk: np.ndarray, prob: float) -> Event:
        """Feed one 512-sample chunk with its VAD probability."""
        if self.state == State.DONE:
            return Event.NONE

        if self.state == State.IDLE:
            self._ring.append(chunk)
            self._idle_chunks += 1
            self._speech_run = self._speech_run + 1 if prob >= self._start_prob else 0
            if self._speech_run >= self._min_speech:
                self.state = State.SPEECH
                self._speech_chunks = list(self._ring)
                self._silence_run = 0
                return Event.SPEECH_START
            if self._max_wait is not None and self._idle_chunks >= self._max_wait:
                self.state = State.DONE
                return Event.TIMEOUT
            return Event.NONE

        # State.SPEECH
        self._speech_chunks.append(chunk)
        self._silence_run = self._silence_run + 1 if prob < self._end_prob else 0
        if (
            self._silence_run >= self._min_silence
            or len(self._speech_chunks) >= self._max_utterance
        ):
            self.state = State.DONE
            return Event.SPEECH_END
        return Event.NONE

    def utterance(self) -> np.ndarray:
        """The captured utterance (pre-roll included). Valid after SPEECH_END."""
        if not self._speech_chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._speech_chunks).astype(np.float32, copy=False)
