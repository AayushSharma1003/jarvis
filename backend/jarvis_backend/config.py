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

    ddir = data_dir()
    ddir.mkdir(parents=True, exist_ok=True)
    return Config(
        ollama_url=ollama_url.rstrip("/"),
        default_model=default_model,
        config_path=path,
        data_dir=ddir,
    )
