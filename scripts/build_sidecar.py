"""Build the PyInstaller onedir sidecar into backend/dist/jarvis-backend/,
where tauri.conf.json's bundle.resources picks it up.

Run from backend/:  uv run python ../scripts/build_sidecar.py
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
SPEC = ROOT / "scripts" / "sidecar.spec"
OUT = BACKEND / "dist" / "jarvis-backend"

# Packages whose data files are resolved from `Path(__file__).parent` at
# RUNTIME, which PyInstaller's static analysis cannot see. Each needs an
# explicit collect_data_files in sidecar.spec, and each has been observed
# breaking a built bundle when it was missing — see the spec's comments.
#
# The check below is DERIVED from the installed packages rather than being a
# hardcoded file list, so a dependency that renames or adds a data file is
# followed automatically instead of silently slipping through.
DATA_PACKAGES = ("kokoro_onnx", "espeakng_loader", "language_tags")


def check_bundled_package_data() -> list[str]:
    """Report any package data file present in the venv but missing from the bundle.

    This is the gate the ready-line smoke test cannot be: the voice stack loads
    lazily (Kokoro is deliberately kept out of RealVoiceIO.load() — see the
    gotcha about it starving the microphone), so the sidecar starts and prints
    `ready` perfectly well with its entire TTS dependency tree absent, and only
    dies the first time the user asks it to speak. That failure is swallowed
    into a log line, so a packaged build ships mute and CI stays green.
    """
    problems: list[str] = []
    for pkg in DATA_PACKAGES:
        module = importlib.import_module(pkg)
        if not module.__file__:
            problems.append(f"{pkg}: not importable from the build venv")
            continue
        source = Path(module.__file__).parent
        want = {
            p.relative_to(source)
            for p in source.rglob("*")
            if p.is_file() and p.suffix not in {".py", ".pyc"}
        }
        bundled = OUT / "_internal" / pkg
        have = (
            {p.relative_to(bundled) for p in bundled.rglob("*") if p.is_file()}
            if bundled.is_dir()
            else set()
        )
        if missing := sorted(want - have):
            shown = ", ".join(str(m) for m in missing[:3])
            more = f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""
            problems.append(
                f"{pkg}: {len(missing)} data file(s) missing from the bundle: {shown}{more}"
                f"\n    fix: add collect_data_files({pkg!r}) to scripts/sidecar.spec"
            )
    return problems


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

    if problems := check_bundled_package_data():
        print("error: bundled package data is incomplete:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"bundled package data ok: {', '.join(DATA_PACKAGES)}")

    # Smoke test: the bundle must at least start and print its ready line.
    #
    # Pointed at a throwaway data/config dir, never the developer's real one.
    # Booting the sidecar is not read-only — it opens (and creates) the
    # database and seeds the bundled extensions — and a build script has no
    # business writing into ~/Library/Application Support/jarvis. It also makes
    # this an honest first-run simulation, on an empty dir like a new install.
    with tempfile.TemporaryDirectory(prefix="jarvis-smoke-") as scratch:
        env = {
            "JARVIS_WS_TOKEN": "smoke",
            "PATH": "/usr/bin:/bin",
            "JARVIS_DATA_DIR": str(Path(scratch) / "data"),
            "JARVIS_CONFIG_DIR": str(Path(scratch) / "config"),
        }
        env |= (
            {"SYSTEMROOT": "C:\\Windows"}
            if sys.platform == "win32"
            else {"HOME": str(Path.home())}
        )
        proc = subprocess.Popen([str(exe)], stdout=subprocess.PIPE, env=env, text=True)
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
