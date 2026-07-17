# models/ — bundled small models

Small models shipped inside the app bundle (NOT LLM weights — those are Ollama's job):

- `hey_jarvis.onnx`, `hey_friday.onnx` — openWakeWord models (~1-3MB each). `hey_friday` is trained once via `scripts/train_wake_word.py` and committed as an artifact to releases, not to git.
- `silero_vad.onnx` — voice activity detection.
- Kokoro ONNX + voice packs (~90MB) — too big for git; fetched at build time.

Weights are **gitignored**; `scripts/fetch_models.py` downloads them (pinned URLs + SHA-256 checks) for dev and CI builds.
