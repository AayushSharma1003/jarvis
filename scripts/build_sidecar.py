"""Build the PyInstaller onedir sidecar into backend/dist/jarvis-backend/,
where tauri.conf.json's bundle.resources picks it up.

Run from backend/:  uv run python ../scripts/build_sidecar.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
SPEC = ROOT / "scripts" / "sidecar.spec"
OUT = BACKEND / "dist" / "jarvis-backend"


def main() -> int:
    shutil.rmtree(BACKEND / "dist", ignore_errors=True)
    shutil.rmtree(BACKEND / "build", ignore_errors=True)

    cmd = [
        "uv",
        "run",
        "--with",
        "pyinstaller>=6.10",
        "pyinstaller",
        str(SPEC),
        "--distpath",
        str(BACKEND / "dist"),
        "--workpath",
        str(BACKEND / "build"),
        "--noconfirm",
    ]
    result = subprocess.run(cmd, cwd=BACKEND)
    if result.returncode != 0:
        return result.returncode

    exe = OUT / ("jarvis-backend.exe" if sys.platform == "win32" else "jarvis-backend")
    if not exe.exists():
        print(f"error: expected executable missing: {exe}", file=sys.stderr)
        return 1

    # Smoke test: the bundle must at least start and print its ready line.
    proc = subprocess.Popen(
        [str(exe)],
        stdout=subprocess.PIPE,
        env={"JARVIS_WS_TOKEN": "smoke", "PATH": "/usr/bin:/bin", "SYSTEMROOT": "C:\\Windows"}
        if sys.platform == "win32"
        else {"JARVIS_WS_TOKEN": "smoke", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
        text=True,
    )
    try:
        line = proc.stdout.readline()
        if '"event": "ready"' not in line and '"event":"ready"' not in line:
            print(f"error: sidecar smoke test failed, first line: {line!r}", file=sys.stderr)
            return 1
        print(f"sidecar ok: {line.strip()}")
    finally:
        proc.kill()
        proc.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
