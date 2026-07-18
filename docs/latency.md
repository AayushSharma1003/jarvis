# Latency Budget

Metric: **end of user speech → first audible response**. Wake→listening is separate (<300ms).

Targets: **<1.5s** on 8GB M2 Pro with 3B tier; **<2s** on 16GB with 7B tier.

| Stage | Budget | Measured (8GB M2, Phase 2) | Notes |
|---|---|---|---|
| VAD endpointing hangover | ~700ms | 700ms (config) | Reported separately from the budget: it's a perception tunable, not pipeline cost. Too aggressive clips slow talkers |
| STT finalization | ~200-300ms | **~140ms** | whisper base on Metal, whole utterance at endpoint (no streaming re-transcription — not needed at this speed) |
| LLM time-to-first-sentence | ~400-600ms | **~500-650ms** | Voice mode appends a "short opening sentence" instruction — it's a latency feature |
| TTS first chunk (Kokoro) | ~150-300ms | **~550-850ms** | fp32 model (int8 is 2.4× SLOWER on Apple Silicon, RTF 0.66 vs 0.28). First chunk = first clause or 10 words, whichever closes first |
| Audio pipeline start | ~50ms | ~40ms | Output stream stays open; barge-in clears the buffer instantly |

**Measured end-to-end (Phase 2, 8GB M2, llama3.2:3b): 1.17–1.41s** — inside the 1.5s budget, with TTS as the dominant term.

Every stage is instrumented; `jarvis doctor --latency` runs the real pipeline on a synthetic utterance (Kokoro speaks the test question; no mic needed) and prints the breakdown. Regressions here are release blockers.

The 3.92s→1.3s Phase 2 path, for posterity: wait-for-full-first-sentence was the killer
(one 4.8s opener = 3.0s of synth). Fixes: clause/word-cap first chunk, fp32 Kokoro,
voice-mode system prompt. CoreML EP was tried and is NOT faster (graph fragments into
155 partitions); stay on CPUExecutionProvider.

Known spike (accepted, must be surfaced in UI): first vision call on 8GB swaps the text model out for the vision model in Ollama — 10-20s. The UI shows "loading vision model", never a bare spinner.
