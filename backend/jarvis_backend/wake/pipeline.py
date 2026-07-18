"""VAD-gated wake detection: the expensive chain only runs around speech.

The always-on CPU story (8 GB M2, efficiency-core clocks):
  mic stream ~1.4% + Silero VAD ~1% ≈ 2.4% idle in a quiet room,
versus ~10% if the melspec→embedding→classifier chain ran continuously
(the embedding model alone is ~90% of the chain's cost). Silero is 13×
cheaper per second of audio than the chain, so it stands guard: the chain
wakes only when speech probability crosses a low bar, sees a pre-roll of
buffered audio (VAD onset lag must not clip the "hey"), and goes back to
sleep after a hangover of silence.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

import numpy as np

VAD_CHUNK = 512  # the only chunk size Silero v5 supports at 16 kHz

# Low bar on purpose: this gate exists to save CPU in silence, not to judge
# speech. False "speech" just costs a moment of chain compute.
VAD_THRESHOLD = 0.2
PREROLL_S = 0.5  # audio replayed into the chain at speech onset
HANGOVER_S = 1.0  # continued silence before the chain sleeps again

# The energy gate in front of the VAD adapts to the room: a rolling noise-
# floor estimate (rises slowly so speech can't drag it up, falls fast so a
# closed door helps immediately); chunks below GATE_OVER_NOISE × floor skip
# the VAD entirely. RMS_FLOOR is the absolute minimum gate for dead-silent
# mics. Speech at conversational distance is 10–100× ambient RMS.
RMS_FLOOR = 1e-3
GATE_OVER_NOISE = 2.5
_NOISE_RISE = 0.005  # EMA alpha upward (~6 s to adopt a louder room)
_NOISE_FALL = 0.1  # EMA alpha downward (quiet again in ~1 s)
# While gated, run the VAD on every Nth chunk that passes the energy gate.
# The preroll replays the skipped audio anyway; onset just lands one later.
IDLE_VAD_STRIDE = 2


class WakePipeline:
    """Sync, hardware-free: feed float32 mono 16 kHz chunks (multiples of
    512 samples); returns the latest wake score when the chain ran, None
    while gated or accumulating. One instance per capture stream."""

    def __init__(
        self,
        vad_prob: Callable[[np.ndarray], float],
        detector,  # wake.detector.WakeDetector shape
        *,
        vad_threshold: float = VAD_THRESHOLD,
        preroll_s: float = PREROLL_S,
        hangover_s: float = HANGOVER_S,
        rms_floor: float = RMS_FLOOR,
        idle_vad_stride: int = IDLE_VAD_STRIDE,
    ) -> None:
        self._vad_prob = vad_prob
        self._detector = detector
        self._vad_threshold = vad_threshold
        self._preroll_samples = int(preroll_s * 16_000)
        self._hangover_samples = int(hangover_s * 16_000)
        self._rms_floor = rms_floor
        self._idle_vad_stride = idle_vad_stride
        self.reset()

    def reset(self) -> None:
        self._preroll: deque[np.ndarray] = deque()
        self._preroll_size = 0
        self._active = False
        self._quiet_samples = 0
        self._vad_tick = 0
        self._noise = 0.0  # rolling ambient-RMS estimate (starts pessimistic)
        self._detector.reset()

    def _energy_gate(self, rms: float) -> float:
        alpha = _NOISE_RISE if rms > self._noise else _NOISE_FALL
        self._noise += alpha * (rms - self._noise)
        return max(self._rms_floor, GATE_OVER_NOISE * self._noise)

    def _speech_in(self, chunk: np.ndarray) -> bool:
        """Cheapest test first: adaptive energy gate (near-free) → VAD
        (decimated while gated; every chunk while the chain is awake, for a
        sharp hangover)."""
        speech = False
        for i in range(0, chunk.size - VAD_CHUNK + 1, VAD_CHUNK):
            piece = chunk[i : i + VAD_CHUNK]
            if not self._active:
                rms = float(np.sqrt(np.mean(piece * piece)))
                if rms < self._energy_gate(rms):
                    continue
                self._vad_tick += 1
                if self._vad_tick % self._idle_vad_stride:
                    continue
            if self._vad_prob(piece) >= self._vad_threshold:
                speech = True
        return speech

    def process(self, chunk: np.ndarray) -> float | None:
        speech = self._speech_in(chunk)

        if not self._active:
            self._preroll.append(chunk)
            self._preroll_size += chunk.size
            while self._preroll_size - self._preroll[0].size >= self._preroll_samples:
                self._preroll_size -= self._preroll.popleft().size
            if not speech:
                return None
            # Speech onset: wake the chain and replay the buffered lead-in so
            # it hears the phrase from the top.
            self._active = True
            self._quiet_samples = 0
            self._detector.reset()
            buffered = np.concatenate(self._preroll)
            self._preroll.clear()
            self._preroll_size = 0
            return self._detector.feed(buffered)

        self._quiet_samples = 0 if speech else self._quiet_samples + chunk.size
        score = self._detector.feed(chunk)
        if self._quiet_samples >= self._hangover_samples:
            self._active = False
        return score
