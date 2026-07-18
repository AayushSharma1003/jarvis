"""TTS engine interface. Engines return raw float32 PCM; playback lives in audio/."""

from __future__ import annotations

from typing import Protocol

import numpy as np


class TTSError(Exception):
    """Raised with a machine-readable code; the frontend translates codes."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class TTSEngine(Protocol):
    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """Return (mono float32 samples in [-1, 1], sample_rate)."""
        ...
