# Installing an Unsigned App

JARVIS v1 ships unsigned — it's a zero-budget open-source project and code-signing certificates cost real money (Apple: $99/yr; Windows EV certs: several hundred). We're not embarrassed about this; here's exactly what your OS will say and what to do.

> **Verification status (2026-07-29, M6.2).** The download and checksum steps below are verified against a real published release. The macOS Gatekeeper *dialogs* are still **not** verified by a human, and the screenshots are still missing — see "What has and hasn't been checked" at the bottom. Nothing here is claimed as confirmed unless it says so.

## macOS

**Install to `/Applications`.** Mount the `.dmg` and drag `Jarvis.app` onto the `Applications` shortcut in the same window. Do not run it from inside the disk image or from `Downloads` — see "App Translocation" below.

**Note:** since macOS 15 (Sequoia), the old right-click → Open trick **no longer bypasses Gatekeeper.** The expected flow:

1. Open the app; macOS blocks it ("Apple could not verify…").
2. **System Settings → Privacy & Security** → scroll down → **"Open Anyway"** next to the JARVIS message.
3. Confirm once. Never asked again for this version.

<!-- SCREENSHOT PLACEHOLDER: the macOS "could not verify" dialog -->
<!-- SCREENSHOT PLACEHOLDER: System Settings > Privacy & Security > Open Anyway -->

### What "unsigned" means here, precisely

The main executable **is** ad-hoc signed — Apple Silicon refuses to run anything else, and Tauri applies it automatically. `codesign -dv` reports `Signature=adhoc`, `TeamIdentifier=not set`, which is accurate.

What the bundle does **not** have is a sealed resource directory: there is no `Contents/_CodeSignature/CodeResources`, so `codesign --verify --strict` reports *"code has no resources but signature indicates they must be present"* and `spctl --assess` reports the same. This is true of every build — local, and every release artifact — and it is a consequence of bundling without a signing identity, not of anything in this repo. It is recorded because it is the difference between macOS saying *"cannot be verified"* (which "Open Anyway" fixes) and *"is damaged"* (which it does not), and **which of those a first-time user sees has not yet been confirmed.**

### App Translocation

If you launch a quarantined `Jarvis.app` from `Downloads` or from inside the mounted `.dmg`, macOS runs it from a randomised read-only path under `/private/var/folders/…/AppTranslocation/…` instead of where you put it. Two consequences, both real:

- Jarvis's bundled espeak data path lands at **~206 characters**, past the 151-character limit at which espeak-ng terminates the process (see the espeak note in `docs/HANDOFF.md`). The fallback that copies the data elsewhere is designed for exactly this, but this path has never been exercised end to end.
- Dragging the app to `/Applications` first avoids translocation entirely, which is why the instruction above is to install before launching.

## Windows

1. SmartScreen: "Windows protected your PC" → click **More info** → **Run anyway**.
2. Only appears on first run per download.

## Linux

No gatekeeping — install the `.deb` normally:

```sh
sudo apt install ./Jarvis_*_amd64.deb
```

Built on Ubuntu 22.04, so the glibc floor is 2.35. There is **no AppImage**: the
build never once succeeded, and shipping the `.deb` was the honest call rather
than spending more release cycles on it.

## Verifying what you downloaded

Every release publishes SHA-256 checksums. Compare before running:

```sh
shasum -a 256 Jarvis_*.dmg   # macOS
sha256sum Jarvis_*.deb       # Linux
Get-FileHash Jarvis_*.msi    # Windows PowerShell
```

Compare the output against the matching line in `SHA256SUMS.txt`, or check the
whole file at once:

```sh
shasum -a 256 --ignore-missing -c SHA256SUMS.txt
```

## What has and hasn't been checked

Stated plainly, because a doc that walks you through a dialog nobody has seen is
worse than one that admits it.

| Step | Status |
|---|---|
| Release publishes `.dmg` / `.msi` / `.exe` / `.deb` + `SHA256SUMS.txt` | ✅ verified on a real release |
| The checksum commands above, run against a downloaded `.dmg` | ✅ verified — matched |
| `codesign -dv` reports ad-hoc, no team identifier | ✅ verified |
| No sealed `_CodeSignature`; `codesign --verify --strict` fails | ✅ verified (see above) |
| The macOS "could not verify" dialog and "Open Anyway" | ❌ **not yet seen by a human** |
| Whether macOS says "cannot be verified" or "is damaged" | ❌ **unknown, and it matters** |
| Windows SmartScreen flow | ❌ never run |
| Linux `.deb` install | ❌ never run |
