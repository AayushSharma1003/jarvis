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
        espeak_root: Path | None = None,
    ):
        for p in (model_path, voices_path):
            if not p.is_file():
                raise TTSError("TTS_MODEL_MISSING", str(p))
        try:
            import espeakng_loader
            from kokoro_onnx import Kokoro
            from kokoro_onnx.config import EspeakConfig
        except ImportError as e:
            raise TTSError("TTS_RUNTIME_MISSING", str(e)) from e

        # espeak-ng exits the process outright when its data path is too long,
        # which is reachable in a packaged .app installed anywhere but
        # /Applications. tts/espeak.py explains it at length; `None` here is
        # the ordinary case and leaves kokoro_onnx to resolve the path itself,
        # exactly as before this existed.
        from ..config import data_dir
        from .espeak import usable_data_path

        try:
            bundled = espeakng_loader.get_data_path()
        except RuntimeError as e:  # the loader's own "data path not exists"
            raise TTSError("TTS_ESPEAK_DATA_MISSING", str(e)) from e
        short = usable_data_path(bundled, espeak_root or data_dir())

        self._kokoro = Kokoro(
            str(model_path),
            str(voices_path),
            espeak_config=EspeakConfig(data_path=short) if short else None,
        )
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
