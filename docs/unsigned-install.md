# Installing an Unsigned App

JARVIS v1 ships unsigned — it's a zero-budget open-source project and code-signing certificates cost real money (Apple: $99/yr; Windows EV certs: several hundred). We're not embarrassed about this; here's exactly what your OS will say and what to do.

> **Verification status (2026-07-29, M6.2).** The download and checksum steps below are verified against a real published release. The macOS Gatekeeper *dialogs* are still **not** verified by a human, and the screenshots are still missing — see "What has and hasn't been checked" at the bottom. Nothing here is claimed as confirmed unless it says so.

## macOS

**Install to `/Applications`.** Mount the `.dmg` and drag `Jarvis.app` onto the `Applications` shortcut in the same window. Do not run it from inside the disk image or from `Downloads` — see "App Translocation" below.

**Note:** since macOS 15 (Sequoia), the old right-click → Open trick **no longer bypasses Gatekeeper.** The flow, as observed on **macOS 26.5.2**:

1. Open the app. A dialog appears, titled **`"Jarvis" Not Opened`**:

   > Apple could not verify "Jarvis" is free of malware that may harm your Mac or compromise your privacy.

   Its two buttons are **`Done`** and **`Move to Bin`**. **Click `Done`.** Do not click `Move to Bin` — it deletes the app, and nothing is wrong with it. This dialog is what *every* unsigned app shows; it is about the absence of Apple notarization, not about anything being broken.

2. **System Settings → Privacy & Security** → scroll to **Security**. There is a line reading *"Jarvis was blocked to protect your Mac"* with an **`Open Anyway`** button. Click it and authenticate. Shortcut to that pane:

   ```sh
   open "x-apple.systempreferences:com.apple.preference.security?General"
   ```

3. Launch Jarvis again and confirm once. Never asked again for this version.

4. **Approve the microphone when macOS asks.** Wake word and push-to-talk both need it. If you are upgrading from any earlier build you *will* be asked again even though you granted it before — v0.1.0-rc5 changed the app's signing identity, and macOS keys every permission to that identity, so the old grant no longer applies to the new app.

**If you instead see `"Jarvis" is damaged and can't be opened`**, you have a build from **v0.1.0-rc4 or earlier**. That is a different failure and step 2 cannot fix it — the bundle signature was broken (see below). Download rc5 or later.

**If Jarvis never hears you** — no reaction to "Hey Jarvis", and ⌘M always ending in "didn't catch that" — check System Settings → Privacy & Security → **Microphone**. If Jarvis is absent from that list entirely and you are never prompted, you are on a build older than rc5 that was signed without the microphone entitlement; there is nothing to grant and only a newer build fixes it.

> **A note on `Open Anyway` and quarantine.** Approving does **not** remove the `com.apple.quarantine` attribute — it sets an "approved" bit and leaves the flag. That matters because App Translocation (below) keys on the flag being *present*, so an approved app can still run from a randomised read-only path. Installing to `/Applications` with a Finder **drag** before first launch avoids the whole situation; `cp -R` from a shell does not, because translocation applies to any quarantined bundle the *user* has not moved.

<!-- SCREENSHOT PLACEHOLDER: the "Jarvis Not Opened" dialog (wording transcribed above) -->
<!-- SCREENSHOT PLACEHOLDER: System Settings > Privacy & Security > Open Anyway -->

### What "unsigned" means here, precisely

The app is **ad-hoc signed**: `codesign -dv` reports `Signature=adhoc` and `TeamIdentifier=not set`, and `codesign --verify --deep --strict` passes. That is the correct state for an unsigned release — Apple Silicon will not run anything less, and the bundle's resource seal is internally consistent, so Gatekeeper's complaint is about *notarization* and nothing else. That is what makes "Open Anyway" work.

**Every release before v0.1.0-rc5 was NOT in that state, and it mattered.** Tauri was never told to sign the bundle, so `Contents/_CodeSignature/CodeResources` was missing while the code directory declared that resources must be sealed. `codesign --verify --strict` failed on every artifact the project ever produced. A user who downloaded such a build got **"Jarvis is damaged and can't be opened. You should move it to the Trash."** — which "Open Anyway" cannot rescue, because it is a broken-signature error rather than an unnotarized one. Fixed by `bundle.macOS.signingIdentity: "-"` in `tauri.conf.json`, and now enforced by a `codesign --verify --deep --strict` gate in `release.yml` so it cannot regress silently. See gotcha 34 in `docs/HANDOFF.md`.

It hid for so long because the two failure modes are invisible without a quarantine flag: artifacts fetched with `gh run download` carry no `com.apple.quarantine`, so Gatekeeper is never consulted and a broken bundle launches perfectly.

### App Translocation

If you launch a quarantined `Jarvis.app` from `Downloads` or from inside the mounted `.dmg`, macOS runs it from a randomised read-only path under `/private/var/folders/…/AppTranslocation/…` instead of where you put it. Two consequences, both real:

- Jarvis's bundled espeak data path lands at **206 characters** (measured, not estimated), past the 151-character limit at which espeak-ng terminates the process (see the espeak note in `docs/HANDOFF.md`). The fallback that copies the data to the data directory is designed for exactly this, and **it holds**: a translocated build was observed running with a live sidecar, resolving espeak from a 69-character path instead. One caveat — the copy already existed from an earlier long-path run, so the *first-run* copy under translocation still has not been exercised.
- Dragging the app to `/Applications` first avoids translocation entirely, which is why the instruction above is to install before launching.

## Windows

1. SmartScreen: "Windows protected your PC" → click **More info** → **Run anyway**.
2. Only appears on first run per download.

## Linux

No gatekeeping — install the `.deb` normally:

```sh
sudo apt install ./Jarvis*.deb
```

Built on Ubuntu 22.04, so the glibc floor is 2.35. There is **no AppImage**: the
build never once succeeded, and shipping the `.deb` was the honest call rather
than spending more release cycles on it.

## Verifying what you downloaded

Every release publishes SHA-256 checksums. Compare before running:

```sh
shasum -a 256 Jarvis*.dmg   # macOS
sha256sum Jarvis*.deb       # Linux
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
| The `"Jarvis" Not Opened` dialog on a **fixed** build (step 1) | ✅ **observed on macOS 26.5.2**, wording transcribed above |
| "Open Anyway" actually launching it (steps 2–3) | ⬅ in progress |
| Screenshots | ❌ two placeholders remain |
| Windows SmartScreen flow | ❌ never run |
| Linux `.deb` install | ❌ never run |
