"""Keep espeak-ng's data path short enough that espeak-ng will accept it.

espeak-ng copies the data path into a fixed-size buffer and calls `exit()` when
it does not fit. It does not raise, does not return an error code, and prints
only a confusing line naming a path from the machine espeak was *compiled* on:

    Error processing file '/Users/runner/work/espeakng-loader/.../phontab'

So this cannot be handled with a try/except -- by the time anything could catch
it the process is gone. The whole sidecar dies the first time Jarvis is asked
to speak, and the user sees "Backend didn't start in time" with no clue why.
The only defense is to check the length *before* handing the path over.

Reachable in packaged builds only, and easily: inside the .app the path is 83
characters after the bundle root, so /Applications is fine (107) but
~/Downloads/jarvis-v0.1.0-macos-arm64-unsigned/Jarvis.app is not (157). Running
the app straight out of target/release/bundle/macos/ is 179 and also fatal,
which is how this was found.

Measured on the 8GB M2 with real directories -- NOT symlinks, because
phonemizer resolves a symlink before espeak sees it and the short target hides
the bug entirely.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .base import TTSError

# 151 characters works, 152 exits the process. Measured, not inferred.
MAX_DATA_PATH_CHARS = 151

DATA_DIR_NAME = "espeak-ng-data"


def usable_data_path(bundled: str, short_root: Path) -> str | None:
    """Return a data path espeak-ng will accept, or None to use its own default.

    `None` is the common case and means "the bundled path is short enough,
    leave kokoro_onnx to resolve it" -- so an installed-to-/Applications user
    pays nothing at all. Only when the bundled path is over the limit is the
    directory copied under `short_root` (the data dir, ~69 characters), once.

    The copy is a real copy on purpose. A symlink would be resolved back to the
    long path before espeak ever saw it, which would look like a fix and change
    nothing.
    """
    source = Path(bundled)
    if not source.is_dir():
        raise TTSError("TTS_ESPEAK_DATA_MISSING", bundled)

    if len(bundled) <= MAX_DATA_PATH_CHARS:
        return None

    destination = short_root / DATA_DIR_NAME
    if len(str(destination)) > MAX_DATA_PATH_CHARS:
        # Nowhere left to put it. Refusing loses speech; continuing would hand
        # espeak a path already known to be fatal and lose the whole sidecar.
        raise TTSError("TTS_ESPEAK_PATH_TOO_LONG", str(destination))

    if not destination.is_dir():
        short_root.mkdir(parents=True, exist_ok=True)
        # copy to a temp name and rename, so an interrupted copy is never left
        # looking like a complete one on the next launch.
        staging = short_root / f".{DATA_DIR_NAME}.partial"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(source, staging, symlinks=False)
        staging.rename(destination)

    return str(destination)
