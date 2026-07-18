"""`jarvis doctor --latency` — measure the voice pipeline against its budget.

Runs the whole loop offline and mic-free: Kokoro speaks a test question, that
audio goes through VAD + whisper, the transcript goes to the real LLM, and the
first reply sentence goes back through Kokoro. What's reported is the part the
user feels: end-of-speech → first audible sample.

    first_audio = STT(utterance) + LLM(first sentence) + TTS(first sentence)

The VAD hangover (~0.7 s of required silence before the endpoint fires) is
reported separately — it's a tunable perception constant, not pipeline cost.

Budget (docs/latency.md): < 1.5 s on the 8 GB M2. Warm numbers are what
matter; every engine gets one warmup call first, like the running app.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import numpy as np

QUESTION = "What can you help me with today?"
BUDGET_S = 1.5
_WARN_MARGIN = 0.85  # warn above 85% of budget: no headroom left on colder days


@dataclass(frozen=True)
class Stage:
    name: str
    seconds: float
    note: str = ""


def _resample(samples: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    n_out = int(len(samples) * sr_to / sr_from)
    return np.interp(
        np.linspace(0, len(samples) - 1, n_out), np.arange(len(samples)), samples
    ).astype(np.float32)


def run_latency(model: str | None = None) -> tuple[list[Stage], float, str]:
    """Returns (stages, first_audio_seconds, status: ok|warn|fail)."""
    from ..assets import missing, path_for
    from ..config import load
    from ..llm.ollama import OllamaBackend
    from ..llm.tiering import pick_model
    from ..stt.transcriber import Transcriber
    from ..stt.vad import CHUNK_SAMPLES, SileroVAD
    from ..tts.chunker import SentenceChunker
    from ..tts.kokoro import KokoroTTS

    if absent := missing():
        raise RuntimeError(
            "voice models missing: " + ", ".join(a.name for a in absent)
            + " — run scripts/fetch_models.py"
        )

    stages: list[Stage] = []

    t0 = time.perf_counter()
    vad = SileroVAD(path_for("silero-vad"))
    stt = Transcriber(path_for("whisper-base"))
    tts = KokoroTTS(path_for("kokoro-model"), path_for("kokoro-voices"))
    stages.append(Stage("engine load", time.perf_counter() - t0, "once per app start"))

    # Warmup: Metal shader compile (whisper) + ORT graph setup (kokoro).
    t0 = time.perf_counter()
    stt.transcribe(np.zeros(16_000, dtype=np.float32))
    tts.synthesize("Warming up.")
    stages.append(Stage("engine warmup", time.perf_counter() - t0, "once per app start"))

    # A synthetic utterance: TTS speech is what the mic would have heard.
    spoken, sr = tts.synthesize(QUESTION)
    utterance = _resample(spoken, sr, 16_000)

    t0 = time.perf_counter()
    for i in range(0, len(utterance) - CHUNK_SAMPLES, CHUNK_SAMPLES):
        vad.prob(utterance[i : i + CHUNK_SAMPLES])
    vad_s = time.perf_counter() - t0
    stages.append(
        Stage("vad", vad_s, f"{len(utterance) / 16_000:.1f}s audio, runs during capture")
    )

    t0 = time.perf_counter()
    heard = stt.transcribe(utterance)
    stt_s = time.perf_counter() - t0
    stages.append(Stage("stt", stt_s, f"heard: {heard[:60]!r}"))

    # First reply sentence from the real LLM (streamed, like the app).
    config = load()
    backend = OllamaBackend(config.ollama_url)

    async def first_sentence() -> tuple[str, float, float]:
        from ..llm.base import ChatMessage

        chosen = model or pick_model(await backend.list_models(), config.default_model)
        chunker = SentenceChunker()
        t_start = time.perf_counter()
        ttft = 0.0
        try:
            async for delta in backend.stream_chat(
                chosen, [ChatMessage("user", heard or QUESTION)]
            ):
                if ttft == 0.0:
                    ttft = time.perf_counter() - t_start
                if sentences := chunker.feed(delta):
                    return sentences[0], ttft, time.perf_counter() - t_start
            return chunker.flush(), ttft, time.perf_counter() - t_start
        finally:
            await backend.close()

    sentence, ttft_s, llm_s = asyncio.run(first_sentence())
    stages.append(Stage("llm first token", ttft_s))
    stages.append(Stage("llm first sentence", llm_s, f"{sentence[:60]!r}"))

    t0 = time.perf_counter()
    first_audio_samples, _ = tts.synthesize(sentence or "Okay.")
    tts_s = time.perf_counter() - t0
    stages.append(
        Stage("tts first sentence", tts_s, f"{len(first_audio_samples) / 24_000:.1f}s of audio")
    )

    first_audio = stt_s + llm_s + tts_s
    status = "ok" if first_audio <= BUDGET_S * _WARN_MARGIN else (
        "warn" if first_audio <= BUDGET_S else "fail"
    )
    return stages, first_audio, status


def format_latency(stages: list[Stage], first_audio: float, status: str) -> str:
    lines = [" stage                 seconds"]
    for s in stages:
        note = f"  ({s.note})" if s.note else ""
        lines.append(f" {s.name:<20} {s.seconds:>8.2f}{note}")
    lines.append("")
    lines.append(
        f" end-of-speech → first audio: {first_audio:.2f}s"
        f" (budget {BUDGET_S:.1f}s) [{status.upper()}]"
    )
    lines.append(" (+~0.7s VAD silence hangover before the endpoint fires — tunable)")
    return "\n".join(lines)
