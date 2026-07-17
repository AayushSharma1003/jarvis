# Installing an Unsigned App

JARVIS v1 ships unsigned — it's a zero-budget open-source project and code-signing certificates cost real money (Apple: $99/yr; Windows EV certs: several hundred). We're not embarrassed about this; here's exactly what your OS will say and what to do.

<!-- SCREENSHOT PLACEHOLDERS for each flow below -->

## macOS

**Note:** since macOS 15 (Sequoia), the old right-click → Open trick **no longer bypasses Gatekeeper.** The current flow:

1. Open the app; macOS blocks it ("Apple could not verify…").
2. **System Settings → Privacy & Security** → scroll down → **"Open Anyway"** next to the JARVIS message.
3. Confirm once. Never asked again for this version.

(The app *is* ad-hoc signed — Apple Silicon requires it and Tauri applies it automatically — it just isn't notarized.)

## Windows

1. SmartScreen: "Windows protected your PC" → click **More info** → **Run anyway**.
2. Only appears on first run per download.

## Linux

No gatekeeping. `.AppImage` needs `chmod +x`; `.deb` installs normally.

## Verifying what you downloaded

Every release publishes SHA-256 checksums. Compare before running:

```sh
shasum -a 256 Jarvis_*.dmg   # macOS
sha256sum jarvis_*.AppImage  # Linux
Get-FileHash jarvis_*.msi    # Windows PowerShell
```
