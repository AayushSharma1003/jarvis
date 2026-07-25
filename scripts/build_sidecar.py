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

# Extensions that make a collected file a shared library rather than data.
_LIBRARY_PARTS = {"so", "dylib", "dll"}


def is_shared_library(name: str) -> bool:
    """True for a shared-library filename, version suffixes included.

    Not a `Path.suffix` test: version suffixes are normal for these
    (`libespeak-ng.so.1.52.0`, `libespeak-ng.1.52.0.dylib`), so the marker can
    sit anywhere after the first dot. Not a plain substring test either --
    that would call `notes.sox` a library.

    A false positive is benign: the file still lands at the same destination,
    it is merely handed to PyInstaller's binary dependency analysis, which
    finds nothing in a non-binary. A false NEGATIVE is the expensive one, and
    is what broke the Linux release build.
    """
    return any(part in _LIBRARY_PARTS for part in name.split(".")[1:])


def drop_libraries(collected: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """A `collect_data_files()` result with shared libraries removed.

    Needed only because `collect_data_files` is INCONSISTENT about them across
    platforms -- it excludes anything ending in one of `PyInstaller.compat.
    ALL_SUFFIXES`, which is Python's *extension module* suffix list:

        macOS   ['.py', '.pyc', '.cpython-313-darwin.so', '.abi3.so', '.so']
        Linux   the same shape -- and a Linux shared library IS '.so'

    So `.dylib` and `.dll` sail through and get collected as data, while a
    Linux `.so` is silently dropped. Libraries are collected explicitly by
    `collect_package_libraries` instead; this strips the macOS/Windows copies
    back out so they are not declared twice.
    """
    return [(s, d) for s, d in collected if not is_shared_library(Path(s).name)]


def collect_package_libraries(package_dir: Path, package: str) -> list[tuple[str, str]]:
    """(source, destination) for every shared library inside an installed package.

    Derived from what is actually on disk rather than a hardcoded filename, so
    a dependency that adds or renames a library is followed automatically --
    the same principle as `check_bundled_package_data` below.

    Destinations stay package-relative because that is where the runtime looks:
    `espeakng_loader.get_library_path()` returns
    `Path(__file__).parent / "libespeak-ng.<ext>"`. `collect_dynamic_libs` is
    NOT the answer -- it flattens to the bundle root, which is precisely the
    wrong place.
    """
    libraries = []
    for path in sorted(package_dir.rglob("*")):
        if path.is_file() and is_shared_library(path.name):
            destination = Path(package) / path.relative_to(package_dir).parent
            libraries.append((str(path), str(destination)))
    return libraries


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
