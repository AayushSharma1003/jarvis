"""Kokoro TTS via kokoro-onnx (onnxruntime — same ML runtime as VAD, no torch)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .base import TTSError


class KokoroTTS:
    """Wraps a loaded Kokoro ONNX model. Synthesis is CPU-bound; call from a thread."""

    def __init__(
        self,
        model_path: Path,
        voices_path: Path,
        voice: str = "af_heart",
        speed: float = 1.0,
        lang: str = "en-us",
    ):
        for p in (model_path, voices_path):
            if not p.is_file():
                raise TTSError("TTS_MODEL_MISSING", str(p))
        try:
            from kokoro_onnx import Kokoro
        except ImportError as e:
            raise TTSError("TTS_RUNTIME_MISSING", str(e)) from e
        self._kokoro = Kokoro(str(model_path), str(voices_path))
        if voice not in self._kokoro.get_voices():
            raise TTSError("TTS_VOICE_UNKNOWN", voice)
        self._voice = voice
        self._speed = speed
        self._lang = lang

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32), 24_000
        samples, sample_rate = self._kokoro.create(
            text, voice=self._voice, speed=self._speed, lang=self._lang
        )
        return samples.astype(np.float32, copy=False), int(sample_rate)
