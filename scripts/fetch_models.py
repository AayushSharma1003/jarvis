#!/usr/bin/env python3
"""Fetch the voice models (STT / VAD / TTS / wake) into the JARVIS data dir.

Run from the repo root:
    cd backend && uv run python ../scripts/fetch_models.py

This is the DEVELOPER path. Users who installed a release do not have this
script — it is not in the app bundle, and the frozen sidecar has no CLI — so
they download the models from inside the app instead (the readiness panel's
button, `assets.fetch`). Both routes call the same `assets.download`, so
resume, size checks and pinned sha256 behave identically; only the progress
reporting differs.

User-invoked only, either way: JARVIS never downloads models on its own.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis_backend.assets import (  # noqa: E402
    ASSETS,
    AssetError,
    download,
    is_present,
    models_dir,
)


def _progress(name: str, done: int, total: int) -> None:
    pct = 100 * done // total if total else 0
    print(
        f"\r  {name}: {done // (1 << 20)}MB / {total // (1 << 20)}MB ({pct}%)",
        end="",
        flush=True,
    )
    if done >= total:
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only", metavar="NAME", choices=sorted(ASSETS), help="fetch a single asset"
    )
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()

    targets = [ASSETS[args.only]] if args.only else list(ASSETS.values())
    print(f"Models dir: {models_dir()}")

    failures = 0
    for asset in targets:
        if is_present(asset.name) and not args.force:
            print(f"  {asset.name}: already present, skipping")
            continue
        try:
            download(asset, on_progress=_progress)
        except AssetError as e:
            print(f"\n  {asset.name}: FAILED — {e.code} {e.detail}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
