# scripts/

| Script | Purpose |
|---|---|
| `install.sh` | macOS/Linux installer: detect OS, install/detect Ollama, install app. Readable on purpose — users are told to read it before piping to sh |
| `install.ps1` | Windows equivalent (winget or direct download) |
| `build_sidecar.py` | Drives PyInstaller with `sidecar.spec` (onedir mode), stages output into `app/src-tauri/binaries/` with the target-triple name Tauri expects |
| `sidecar.spec` | Hand-written PyInstaller spec: collects onnxruntime + whisper.cpp dynamic libs. Do not replace with `--onefile` — see app/README.md |
| `fetch_models.py` | Downloads bundled small models (pinned URLs, SHA-256 verified) into `models/` |
| `train_wake_word.py` | **Offline** wake-word training tool (synthetic data generation + openWakeWord training). Runs on a beefy machine (the A6000), never inside the app. The app only ever loads a `.onnx`. If this grows into a subproject, it gets demoted to a documented advanced workflow — per the approved plan |
