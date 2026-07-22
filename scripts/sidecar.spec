# PyInstaller spec for the jarvis-backend sidecar.
#
# ONEDIR ON PURPOSE. Onefile extracts to a temp dir on every launch (slow
# start, orphaned-process bugs under Tauri supervision). Do not "simplify"
# this to --onefile. See app/README.md and docs/architecture.md.
#
# Phase 2 will add collect_dynamic_libs for onnxruntime and whisper.cpp here.

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
