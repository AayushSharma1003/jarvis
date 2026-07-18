"""Configuration: a TOML file in the platform config dir, with env overrides.

Env overrides exist for development and tests:
  JARVIS_CONFIG_DIR  – directory containing config.toml
  JARVIS_DATA_DIR    – directory for the SQLite database and other state
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "jarvis"

DEFAULT_CONFIG = """\
# JARVIS configuration. Missing keys fall back to the defaults shown here.

[llm]
# Base URL of the Ollama server.
ollama_url = "http://127.0.0.1:11434"
# Model to use. Empty string means: auto-select the best installed model
# for this machine's RAM tier (see docs/architecture.md).
default_model = ""

[wake]
# Wake-word ("Hey Jarvis") detection sensitivity, 0-1. Higher = fewer false
# triggers but easier to miss. The on/off toggle itself lives in the app UI
# (persisted in the data dir), not here.
threshold = 0.5
"""


class ConfigError(Exception):
    """Raised with a machine-readable code; the frontend translates codes."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class Config:
    ollama_url: str
    default_model: str
    config_path: Path
    data_dir: Path
    wake_threshold: float = 0.5


def config_dir() -> Path:
    if override := os.environ.get("JARVIS_CONFIG_DIR"):
        return Path(override)
    return platformdirs.user_config_path(APP_NAME)


def data_dir() -> Path:
    if override := os.environ.get("JARVIS_DATA_DIR"):
        return Path(override)
    return platformdirs.user_data_path(APP_NAME)


def load() -> Config:
    """Load config, creating a commented default file on first run."""
    cdir = config_dir()
    path = cdir / "config.toml"
    if not path.exists():
        cdir.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG, encoding="utf-8")

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError("CONFIG_PARSE_ERROR", str(e)) from e

    llm = raw.get("llm", {})
    ollama_url = llm.get("ollama_url", "http://127.0.0.1:11434")
    default_model = llm.get("default_model", "")
    if not isinstance(ollama_url, str) or not isinstance(default_model, str):
        raise ConfigError("CONFIG_INVALID_VALUE", "[llm] values must be strings")

    wake = raw.get("wake", {})
    wake_threshold = wake.get("threshold", 0.5)
    if not isinstance(wake_threshold, int | float) or not 0.0 <= wake_threshold <= 1.0:
        raise ConfigError("CONFIG_INVALID_VALUE", "[wake] threshold must be in [0, 1]")

    ddir = data_dir()
    ddir.mkdir(parents=True, exist_ok=True)
    return Config(
        ollama_url=ollama_url.rstrip("/"),
        default_model=default_model,
        wake_threshold=float(wake_threshold),
        config_path=path,
        data_dir=ddir,
    )


# --- app-managed state ------------------------------------------------------
# Settings the app itself writes (UI toggles) live in state.toml in the DATA
# dir, apart from the hand-edited config.toml — the app never rewrites the
# user's config file, so their comments and edits are never clobbered.


def _state_path() -> Path:
    return data_dir() / "state.toml"


def load_wake_enabled() -> bool:
    try:
        raw = tomllib.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return bool(raw.get("wake", {}).get("enabled", False))


def save_wake_enabled(enabled: bool) -> None:
    import tomli_w

    path = _state_path()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raw = {}
    raw.setdefault("wake", {})["enabled"] = enabled
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_bytes(tomli_w.dumps(raw).encode("utf-8"))
    tmp.replace(path)
