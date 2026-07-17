# Latency Budget

Metric: **end of user speech → first audible response**. Wake→listening is separate (<300ms).

Targets: **<1.5s** on 8GB M2 Pro with 3B tier; **<2s** on 16GB with 7B tier.

| Stage | Budget | Notes |
|---|---|---|
| VAD endpointing hangover | ~400ms | The biggest hidden cost. Silence threshold is a tunable; too aggressive clips slow talkers |
| STT finalization | ~200-300ms | whisper.cpp streams during speech; only the tail is on the clock |
| LLM time-to-first-sentence | ~400-600ms | Short system prompt matters; tool schemas bloat TTFT |
| TTS first chunk (Kokoro) | ~150-300ms | Sentence-chunked; first sentence synthesizes while the LLM continues |
| Audio pipeline start | ~50ms | |

Every stage is instrumented; `jarvis doctor --latency` replays a golden fixture through the pipeline and prints the breakdown. Regressions here are release blockers.

Known spike (accepted, must be surfaced in UI): first vision call on 8GB swaps the text model out for the vision model in Ollama — 10-20s. The UI shows "loading vision model", never a bare spinner.
