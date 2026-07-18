"""Silero VAD v5 via onnxruntime (the one ML runtime — no torch).

Mirrors the official silero-vad OnnxWrapper: 512-sample chunks at 16 kHz,
a 64-sample context carried between calls, and a (2, 1, 128) recurrent state.
Returns a speech probability per chunk; thresholding lives in endpointing.py.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 512  # 32 ms — the only chunk size Silero v5 supports at 16 kHz
_CONTEXT = 64


class VADError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class SileroVAD:
    """Stateful per-stream VAD. Call reset() between utterance streams."""

    def __init__(self, model_path: Path):
        if not model_path.is_file():
            raise VADError("VAD_MODEL_MISSING", str(model_path))
        try:
            import onnxruntime as ort
        except ImportError as e:
            raise VADError("VAD_RUNTIME_MISSING", str(e)) from e
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3  # keep sidecar stderr readable
        self._session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT), dtype=np.float32)

    def prob(self, chunk: np.ndarray) -> float:
        """Speech probability for one 512-sample float32 mono chunk at 16 kHz."""
        if chunk.shape[-1] != CHUNK_SAMPLES:
            raise VADError("VAD_BAD_CHUNK", f"expected {CHUNK_SAMPLES}, got {chunk.shape[-1]}")
        x = np.concatenate(
            [self._context, chunk.reshape(1, -1).astype(np.float32, copy=False)], axis=1
        )
        out, self._state = self._session.run(
            None,
            {"input": x, "state": self._state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)},
        )
        self._context = x[:, -_CONTEXT:]
        return float(out[0][0])
