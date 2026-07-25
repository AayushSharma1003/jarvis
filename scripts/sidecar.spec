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

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

# SPECPATH is injected by PyInstaller and points at this file's directory.
ROOT = Path(SPECPATH).parent  # noqa: F821

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
        #   espeakng_loader ships BOTH the libespeak-ng dylib and the
        #   espeak-ng-data directory, and resolves each with
        #   `Path(__file__).parent / ...` (get_library_path/get_data_path).
        #   collect_data_files — not collect_dynamic_libs — is what is wanted
        #   for the dylib too: it keeps the file package-relative, which is
        #   exactly where that runtime lookup goes. get_data_path() raises
        #   outright when the data directory is absent.
        *collect_data_files("espeakng_loader"),
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
