"""`jarvis doctor` — the diagnostic we live in during development and users
live in when things break. Human-readable by design (this is a dev/debug CLI,
not the app UI; the i18n error-code rule applies to the frontend surface)."""

from __future__ import annotations

import platform
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

OK, WARN, FAIL = "ok", "warn", "fail"
_ICON = {OK: "\033[32m✓\033[0m", WARN: "\033[33m!\033[0m", FAIL: "\033[31m✗\033[0m"}


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str = ""


def run_checks() -> list[Check]:
    checks: list[Check] = [_python(), _machine()]

    from ..config import ConfigError, load

    try:
        config = load()
        checks.append(Check("config", OK, str(config.config_path)))
    except ConfigError as e:
        checks.append(Check("config", FAIL, f"{e.code} {e.detail}"))
        return checks  # everything below needs config

    checks.append(_data_dir(config.data_dir))
    checks.append(_database(config.data_dir))
    checks.extend(_ollama(config.ollama_url, config.default_model))
    checks.append(_port_bindable())
    checks.append(_voice_models())
    checks.append(_audio_devices())
    return checks


def _python() -> Check:
    v = sys.version_info
    status = OK if v >= (3, 11) else FAIL
    return Check("python", status, f"{v.major}.{v.minor}.{v.micro} ({sys.executable})")


def _machine() -> Check:
    from ..llm.tiering import ram_gb, tier_budget_b

    gb = ram_gb()
    return Check(
        "machine",
        OK,
        f"{platform.system()} {platform.machine()}, {gb:.0f} GB RAM"
        f" → model budget ≤{tier_budget_b(gb):g}B params",
    )


def _data_dir(path: Path) -> Check:
    try:
        with tempfile.TemporaryFile(dir=path):
            pass
        return Check("data dir", OK, str(path))
    except OSError as e:
        return Check("data dir", FAIL, f"not writable: {e}")


def _database(data_dir: Path) -> Check:
    from ..storage import db

    try:
        conn = db.connect(data_dir / "jarvis.sqlite3")
        version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        n = conn.execute("SELECT count(*) FROM conversations").fetchone()[0]
        conn.close()
        return Check("database", OK, f"schema v{version}, {n} conversation(s)")
    except Exception as e:  # noqa: BLE001 - doctor reports, never crashes
        return Check("database", FAIL, str(e))


def _ollama(base_url: str, configured_model: str) -> list[Check]:
    from ..llm.base import LLMError, ModelInfo
    from ..llm.tiering import pick_model

    try:
        with httpx.Client(timeout=3.0) as client:
            version = client.get(f"{base_url}/api/version").json().get("version", "?")
            tags = client.get(f"{base_url}/api/tags").json().get("models", [])
    except httpx.HTTPError as e:
        return [
            Check(
                "ollama",
                FAIL,
                f"unreachable at {base_url} ({e.__class__.__name__})."
                " Is Ollama installed and running?",
            )
        ]

    models = [
        ModelInfo(
            id=m["name"],
            parameter_size=(m.get("details") or {}).get("parameter_size"),
            size_bytes=m.get("size"),
        )
        for m in tags
    ]
    checks = [Check("ollama", OK, f"v{version} at {base_url}, {len(models)} model(s) installed")]
    try:
        chosen = pick_model(models, configured_model)
        source = "configured" if configured_model else "auto-selected for RAM tier"
        checks.append(Check("model", OK, f"{chosen} ({source})"))
    except LLMError as e:
        if e.code == "NO_MODELS":
            checks.append(
                Check("model", WARN, "no models installed — onboarding will offer a download")
            )
        else:
            checks.append(Check("model", FAIL, f"{e.code} {e.detail}"))
    return checks


def _voice_models() -> Check:
    from ..assets import ASSETS, missing, models_dir

    absent = missing()
    if not absent:
        return Check("voice models", OK, f"{len(ASSETS)} model(s) in {models_dir()}")
    names = ", ".join(a.name for a in absent)
    return Check(
        "voice models",
        WARN,
        f"missing: {names} — run `uv run python ../scripts/fetch_models.py` (voice disabled)",
    )


def _audio_devices() -> Check:
    try:
        import sounddevice as sd
    except (ImportError, OSError) as e:  # OSError: PortAudio lib missing
        return Check("audio devices", FAIL, f"sounddevice unavailable: {e}")
    try:
        devices = sd.query_devices()
    except sd.PortAudioError as e:
        return Check("audio devices", FAIL, str(e))
    inputs = sum(1 for d in devices if d["max_input_channels"] > 0)
    outputs = sum(1 for d in devices if d["max_output_channels"] > 0)
    if not inputs or not outputs:
        return Check("audio devices", WARN, f"{inputs} input(s), {outputs} output(s)")
    return Check("audio devices", OK, f"{inputs} input(s), {outputs} output(s)")


def _port_bindable() -> Check:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        return Check("loopback bind", OK, f"ephemeral port ok (tested {port})")
    except OSError as e:
        return Check("loopback bind", FAIL, str(e))


def format_checks(checks: list[Check], color: bool = True) -> str:
    lines = []
    for c in checks:
        icon = _ICON[c.status] if color else {OK: "ok", WARN: "warn", FAIL: "FAIL"}[c.status]
        lines.append(f" {icon} {c.name:<14} {c.detail}")
    return "\n".join(lines)
