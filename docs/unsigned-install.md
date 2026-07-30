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

The app is **ad-hoc signed**: `codesign -dv` reports `Signature=adhoc` and `TeamIdentifier=not set`, and `codesign --verify --deep --strict` passes. That is the correct state for an unsigned release — Apple Silicon will not run anything less, and the bundle's resource seal is internally consistent, so Gatekeeper's complaint is about *notarization* and nothing else. That is what makes "Open Anyway" work.

**Every release before v0.1.0-rc5 was NOT in that state, and it mattered.** Tauri was never told to sign the bundle, so `Contents/_CodeSignature/CodeResources` was missing while the code directory declared that resources must be sealed. `codesign --verify --strict` failed on every artifact the project ever produced. A user who downloaded such a build got **"Jarvis is damaged and can't be opened. You should move it to the Trash."** — which "Open Anyway" cannot rescue, because it is a broken-signature error rather than an unnotarized one. Fixed by `bundle.macOS.signingIdentity: "-"` in `tauri.conf.json`, and now enforced by a `codesign --verify --deep --strict` gate in `release.yml` so it cannot regress silently. See gotcha 34 in `docs/HANDOFF.md`.

It hid for so long because the two failure modes are invisible without a quarantine flag: artifacts fetched with `gh run download` carry no `com.apple.quarantine`, so Gatekeeper is never consulted and a broken bundle launches perfectly.

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
| `codesign --verify --deep --strict` passes on the bundle | ✅ verified (rc5 onward; **failed** on rc1–rc4) |
| "is damaged" on a quarantined pre-rc5 build | ✅ **observed by the owner** — the bug this fixed |
| `spctl --assess` on a fixed build returns a plain `rejected` | ✅ verified — the recoverable path, not a signature error |
| The macOS "could not verify" dialog and "Open Anyway" on a **fixed** build | ❌ not yet seen by a human |
| Windows SmartScreen flow | ❌ never run |
| Linux `.deb` install | ❌ never run |
