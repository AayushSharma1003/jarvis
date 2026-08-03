"""Voice model assets: the registry of downloadable models and where they live.

Single source of truth shared by scripts/fetch_models.py (downloads),
`jarvis doctor` (presence checks), and the voice pipeline (load paths).
Models are fetched by explicit user action only — never automatically
(zero phone-home principle). They live under the platform data dir
(env-overridable via JARVIS_DATA_DIR), NOT inside the app bundle.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .config import data_dir


@dataclass(frozen=True)
class Asset:
    name: str
    filename: str
    url: str
    size_bytes: int
    # sha256 pinned at the time the URL was added; guards corrupt/partial
    # downloads and silent upstream swaps. Empty string = not yet pinned.
    sha256: str
    # "voice" (STT/VAD/TTS — the voice loop) or "wake" (always-on wake word).
    # The groups fail independently: missing wake models never disable voice.
    group: str = "voice"


ASSETS: dict[str, Asset] = {
    a.name: a
    for a in (
        Asset(
            name="whisper-base",
            filename="ggml-base.bin",
            url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
            size_bytes=147951465,
            sha256="60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
        ),
        Asset(
            name="silero-vad",
            filename="silero_vad.onnx",
            url="https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx",
            size_bytes=2327524,
            sha256="1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
        ),
        # fp32, not int8: on Apple Silicon the int8 graph is 2.4× SLOWER
        # (RTF 0.66 vs 0.28 measured on the 8 GB M2) — quantized ops fall off
        # the optimized BLAS path. The 233 MB size delta is the price of
        # hitting the first-audio latency budget.
        Asset(
            name="kokoro-model",
            filename="kokoro-v1.0.onnx",
            url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
            size_bytes=325532387,
            sha256="7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
        ),
        Asset(
            name="kokoro-voices",
            filename="voices-v1.0.bin",
            url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
            size_bytes=28214398,
            sha256="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
        ),
        # openWakeWord inference chain (melspectrogram → speech embedding →
        # wake classifier). We run these directly on onnxruntime — see
        # wake/detector.py for why the openwakeword package isn't a dependency.
        Asset(
            name="wake-melspec",
            filename="melspectrogram.onnx",
            url="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/melspectrogram.onnx",
            size_bytes=1087958,
            sha256="ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
            group="wake",
        ),
        Asset(
            name="wake-embedding",
            filename="embedding_model.onnx",
            url="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/embedding_model.onnx",
            size_bytes=1326578,
            sha256="70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
            group="wake",
        ),
        Asset(
            name="wake-hey-jarvis",
            filename="hey_jarvis_v0.1.onnx",
            url="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/hey_jarvis_v0.1.onnx",
            size_bytes=1271370,
            sha256="94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb",
            group="wake",
        ),
    )
}


def models_dir() -> Path:
    return data_dir() / "models"


def path_for(name: str) -> Path:
    return models_dir() / ASSETS[name].filename


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def is_present(name: str) -> bool:
    """Fast presence check: file exists with the expected size."""
    asset = ASSETS[name]
    p = path_for(name)
    return p.is_file() and p.stat().st_size == asset.size_bytes


def missing(group: str | None = None) -> list[Asset]:
    return [
        a
        for a in ASSETS.values()
        if (group is None or a.group == group) and not is_present(a.name)
    ]


class AssetError(Exception):
    """Machine-readable failure — the frontend owns the wording (i18n)."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


CHUNK = 1 << 18  # 256 KiB


def _urlopen(req, timeout: int = 60):
    import urllib.request

    return urllib.request.urlopen(req, timeout=timeout)


def download(asset: Asset, on_progress=None, opener=None) -> None:
    """Fetch one asset into the models dir. Blocking — call it off the loop.

    This lives in the backend rather than in `scripts/` because the packaged
    app has neither: no `scripts/` directory in the bundle and no CLI in the
    frozen sidecar (its entrypoint is the server). A user who installed from a
    release therefore had NO way to obtain the voice models, while the app told
    them to run a script from a repo they never cloned.

    Resumes a partial `.part`, verifies size and pinned sha256, and renames
    atomically — so an interrupted download is never mistaken for a model, and
    a changed upstream file is refused rather than loaded into onnxruntime as a
    mystery crash later.

    **The size is a ceiling during the read, not just a check after it.** The
    sha256 is pinned because upstream is not trusted to serve the right bytes,
    and that distrust has to cover *how many* bytes: verifying afterwards means
    a server that never stops sending has already filled the disk by the time
    anything looks. Same reasoning as `tools/shell.py`'s incremental output cap
    and `tools/web.py`'s read cap — this is the one path in the app that writes
    a large file, on an 8 GB laptop, with no cancel button in front of it.
    """
    import shutil
    import urllib.error
    import urllib.request

    opener = opener or _urlopen
    dest = models_dir() / asset.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    offset = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "jarvis"}
    if 0 < offset < asset.size_bytes:
        headers["Range"] = f"bytes={offset}-"
    elif offset >= asset.size_bytes:
        offset = 0
        part.unlink()

    # Ask before writing. A nearly-full disk is an ordinary state on a laptop,
    # and ENOSPC 200 MB into a 325 MB file reports a generic "download failed"
    # while leaving behind the partial bytes that helped fill it. The margin
    # covers the atomic rename, which needs both copies briefly.
    try:
        free = shutil.disk_usage(dest.parent).free
    except OSError:  # pragma: no cover - unreadable mount; let the write decide
        free = None
    if free is not None and free < (asset.size_bytes - offset) + (1 << 24):
        raise AssetError(
            "ASSET_NO_SPACE", f"{asset.name}: needs {asset.size_bytes} bytes, {free} free"
        )

    overrun = False
    req = urllib.request.Request(asset.url, headers=headers)
    try:
        with opener(req, timeout=60) as resp:
            resuming = getattr(resp, "status", 200) == 206
            done = offset if resuming else 0
            with part.open("ab" if resuming else "wb") as f:
                while chunk := resp.read(CHUNK):
                    f.write(chunk)
                    done += len(chunk)
                    if done > asset.size_bytes:
                        overrun = True
                        break
                    if on_progress is not None:
                        on_progress(asset.name, done, asset.size_bytes)
    except (urllib.error.URLError, OSError) as e:
        raise AssetError("ASSET_DOWNLOAD_FAILED", f"{asset.name}: {e}") from e

    actual = part.stat().st_size
    if actual != asset.size_bytes:
        if overrun:
            # Too long can only ever fail again, and resume would append to it
            # forever. Same reasoning as the checksum branch below.
            part.unlink(missing_ok=True)
        raise AssetError(
            "ASSET_SIZE_MISMATCH", f"{asset.name}: got {actual}, want {asset.size_bytes}"
        )
    if asset.sha256 and file_sha256(part) != asset.sha256:
        # Delete it: a wrong file that survives on disk gets retried forever
        # and looks present to `is_present`, which only checks size.
        part.unlink()
        raise AssetError("ASSET_CHECKSUM_MISMATCH", asset.name)
    part.replace(dest)


def fetch_missing(group: str | None = None, on_progress=None, opener=None) -> list[str]:
    """Download everything not already present. Returns the names that FAILED.

    Keeps going after a failure on purpose: the wake models are tiny and the
    voice models are large, so one flaky 300 MB download should not also cost
    the user the three small files that would have succeeded.
    """
    failed: list[str] = []
    for asset in missing(group):
        try:
            download(asset, on_progress=on_progress, opener=opener)
        except AssetError:
            failed.append(asset.name)
    return failed
