#!/usr/bin/env python3
"""Fetch the voice models (STT / VAD / TTS) into the JARVIS data dir.

Run from the repo root:
    cd backend && uv run python ../scripts/fetch_models.py

User-invoked only — JARVIS never downloads models on its own.
Supports resume (.part + HTTP Range), verifies size and (when pinned) sha256,
and renames atomically so a killed download never leaves a corrupt model.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis_backend.assets import ASSETS, Asset, file_sha256, is_present, models_dir  # noqa: E402

CHUNK = 1 << 18  # 256 KiB


def download(asset: Asset, dest: Path) -> None:
    part = dest.with_suffix(dest.suffix + ".part")
    offset = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "jarvis-fetch-models"}
    if 0 < offset < asset.size_bytes:
        headers["Range"] = f"bytes={offset}-"
    elif offset >= asset.size_bytes:
        offset = 0
        part.unlink()

    req = urllib.request.Request(asset.url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        resuming = resp.status == 206
        mode = "ab" if resuming else "wb"
        done = offset if resuming else 0
        with part.open(mode) as f:
            while chunk := resp.read(CHUNK):
                f.write(chunk)
                done += len(chunk)
                pct = 100 * done // asset.size_bytes
                print(f"\r  {asset.name}: {done // (1 << 20)}MB / "
                      f"{asset.size_bytes // (1 << 20)}MB ({pct}%)", end="", flush=True)
    print()

    actual_size = part.stat().st_size
    if actual_size != asset.size_bytes:
        raise RuntimeError(
            f"{asset.name}: size mismatch (got {actual_size}, want {asset.size_bytes}); "
            "delete the .part file and retry"
        )
    digest = file_sha256(part)
    if asset.sha256 and digest != asset.sha256:
        part.unlink()
        raise RuntimeError(f"{asset.name}: sha256 mismatch — upstream file changed, refusing")
    if not asset.sha256:
        print(f"  {asset.name}: sha256 {digest} (not pinned in assets.py yet)")
    part.replace(dest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", metavar="NAME", choices=sorted(ASSETS),
                        help="fetch a single asset")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()

    targets = [ASSETS[args.only]] if args.only else list(ASSETS.values())
    mdir = models_dir()
    mdir.mkdir(parents=True, exist_ok=True)
    print(f"Models dir: {mdir}")

    failures = 0
    for asset in targets:
        dest = mdir / asset.filename
        if is_present(asset.name) and not args.force:
            print(f"  {asset.name}: already present, skipping")
            continue
        try:
            download(asset, dest)
        except (urllib.error.URLError, RuntimeError, OSError) as e:
            print(f"  {asset.name}: FAILED — {e}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
