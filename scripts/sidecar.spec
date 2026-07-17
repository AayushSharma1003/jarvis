# PyInstaller spec for the jarvis-backend sidecar.
#
# ONEDIR ON PURPOSE. Onefile extracts to a temp dir on every launch (slow
# start, orphaned-process bugs under Tauri supervision). Do not "simplify"
# this to --onefile. See app/README.md and docs/architecture.md.
#
# Phase 2 will add collect_dynamic_libs for onnxruntime and whisper.cpp here.

from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["../backend/sidecar_entry.py"],
    pathex=["../backend"],
    datas=collect_data_files("jarvis_backend", includes=["**/*.sql"]),
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
