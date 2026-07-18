"""Speech-to-text: whisper.cpp via pywhispercpp (Metal on macOS, CUDA/CPU elsewhere).

Loaded once per process and reused — model load + Metal shader warmup is
expensive (seconds); a transcribe call on a few seconds of speech is not
(hundreds of ms on the 8GB M2 target).

Transcription happens once per utterance at the VAD endpoint. This is a
deliberate scope cut vs. "streaming" whisper (re-transcribing a growing
window), which costs CPU/GPU we don't have on the 8GB target; the protocol
keeps room for partials if endpoint-only ever feels dead. Revisit with
doctor --latency numbers in hand.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000


class STTError(Exception):
    """Raised with a machine-readable code; the frontend translates codes."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class Transcriber:
    """Wraps a loaded whisper.cpp model. Not thread-safe; call from one thread."""

    def __init__(self, model_path: Path, language: str = "en", n_threads: int = 4):
        if not model_path.is_file():
            raise STTError("STT_MODEL_MISSING", str(model_path))
        # Import here so the backend still starts (text chat) without pywhispercpp.
        try:
            from pywhispercpp.model import Model
        except ImportError as e:
            raise STTError("STT_RUNTIME_MISSING", str(e)) from e
        self._language = language
        self._model = Model(
            str(model_path),
            n_threads=n_threads,
            print_progress=False,
            print_realtime=False,
        )

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: mono float32 in [-1, 1] at 16 kHz. Returns joined segment text."""
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.size < SAMPLE_RATE // 10:  # <100 ms can confuse decoders; treat as silence
            return ""
        segments = self._model.transcribe(audio, language=self._language)
        return " ".join(s.text.strip() for s in segments).strip()
