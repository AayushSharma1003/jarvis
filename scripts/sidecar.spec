# PyInstaller spec for the jarvis-backend sidecar.
#
# ONEDIR ON PURPOSE. Onefile extracts to a temp dir on every launch (slow
# start, orphaned-process bugs under Tauri supervision). Do not "simplify"
# this to --onefile. See app/README.md and docs/architecture.md.
#
# onnxruntime and whisper.cpp need nothing here: their native libraries are
# reached through load commands on an extension module, which PyInstaller's
# binary dependency analysis follows on its own. (Verified in the built tree —
# libonnxruntime, libwhisper and libggml-metal are all collected.) What it
# CANNOT follow is a path built from `__file__` at runtime; see below.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

# SPECPATH is injected by PyInstaller and points at this file's directory.
ROOT = Path(SPECPATH).parent  # noqa: F821

# These live in build_sidecar.py so they can be unit tested -- a .spec file is
# not importable (backend/tests/test_sidecar_build.py).
sys.path.insert(0, SPECPATH)  # noqa: F821
from build_sidecar import collect_package_libraries, drop_libraries  # noqa: E402

# espeakng_loader ships its shared library AND its espeak-ng-data directory,
# and resolves each with `Path(__file__).parent / ...` at RUNTIME
# (get_library_path/get_data_path), which static analysis never follows. Both
# must therefore land package-relative — but they cannot be collected the same
# way, because collect_data_files is inconsistent across platforms:
# it excludes everything ending in PyInstaller.compat.ALL_SUFFIXES, which is
# Python's *extension module* suffix list. `.dylib` and `.dll` are not in it
# and get collected; a Linux `.so` IS, and is silently dropped. That is why
# macOS and Windows built fine on the first release tag and Linux did not.
# So: libraries explicitly, data through collect_data_files with the
# macOS/Windows library copies stripped back out to avoid declaring them twice.
import espeakng_loader  # noqa: E402

_ESPEAK_DIR = Path(espeakng_loader.__file__).parent
espeak_binaries = collect_package_libraries(_ESPEAK_DIR, "espeakng_loader")
espeak_datas = drop_libraries(collect_data_files("espeakng_loader"))

a = Analysis(
    ["../backend/sidecar_entry.py"],
    pathex=["../backend"],
    datas=[
        *collect_data_files("jarvis_backend", includes=["**/*.sql"]),
        # The curated model catalog. It gates which models may be handed a tool
        # schema (llm/capabilities.py), and a MISSING catalog silently means
        # "no model is trusted with tools" — so if this line goes, tool use
        # quietly disappears from packaged builds while source runs stay fine.
        # llm/catalog.py resolves it via sys._MEIPASS, which onedir points at
        # _internal/, hence the "catalog" destination directory here.
        (str(ROOT / "catalog" / "models.toml"), "catalog"),
        # Package data reached via `Path(__file__).parent / ...` at RUNTIME,
        # which PyInstaller's static analysis cannot see. Each of these is a
        # real, observed failure in a built bundle, not a precaution:
        #
        #   kokoro_onnx/config.json — read at MODULE IMPORT time
        #   (kokoro_onnx/config.py's get_vocab()), so `from kokoro_onnx import
        #   Kokoro` raises FileNotFoundError and the whole TTS stack is dead.
        *collect_data_files("kokoro_onnx"),
        #   language_tags/data/json/*.json — also read at import, three levels
        #   down the phonemizer chain (phonemizer -> segments -> csvw ->
        #   language_tags). Nothing in our code names this package.
        *collect_data_files("language_tags"),
        #   espeakng_loader's data half — see espeak_datas above.
        #   get_data_path() raises outright when this directory is absent.
        *espeak_datas,
        # The extensions that ship with the app. Nothing copied these into the
        # data dir before M6.0, so timers-reminders did not exist for any real
        # user. Resolved at runtime through sys._MEIPASS exactly like the
        # catalog above; WHICH of them get seeded is decided by BUNDLED in
        # extensions/bundled.py, not by what happens to be in this folder.
        *[
            (str(p), f"extensions/{p.relative_to(ROOT / 'extensions').parent}")
            for p in (ROOT / "extensions").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        ],
    ],
    # `collect_dynamic_libs` is deliberately NOT used here: it flattens to the
    # bundle root, and the runtime lookup is package-relative. An explicit
    # (source, "espeakng_loader") destination is the only form that says
    # "a library" and "beside its package" at the same time.
    binaries=[*espeak_binaries],
    hiddenimports=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="jarvis-backend",
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="jarvis-backend",
)
