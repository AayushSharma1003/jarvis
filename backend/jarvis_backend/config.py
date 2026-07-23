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

[tools]
# Master switch for "dangerous" tools (running shell commands, deleting).
# When true they still confirm on EVERY call — this only decides whether they
# may be offered at all. Set it to false and Jarvis will refuse them outright,
# without even asking. Tools marked "ask" (writing a file, reading the
# clipboard) are unaffected and always confirm.
allow_dangerous = true

[filesystem]
# Folders the file tools may touch. Paths are symlink-resolved before the
# check, so a shortcut inside one of these that points elsewhere does NOT
# widen the sandbox. Jarvis's own config and data folders are permanently
# off-limits even if you list a folder containing them.
#
# Leave this key out for the defaults: your Documents, Downloads and Desktop.
# Set it to an empty list to switch file access off entirely.
# roots = ["~/Documents", "~/Downloads", "~/Desktop"]
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
    # §1's "globally disableable". True still means every dangerous call
    # confirms; false means they are refused without asking.
    allow_dangerous_tools: bool = True
    # §2's sandbox roots. An EMPTY tuple means no file access at all, and is
    # reachable only by writing `roots = []` — an absent key gets the defaults
    # (see `default_filesystem_roots`), never this.
    filesystem_roots: tuple[Path, ...] = ()


def default_filesystem_roots() -> tuple[Path, ...]:
    """Where file tools may work when the user hasn't said otherwise.

    Documents / Downloads / Desktop: useful on day one without handing over the
    whole home directory, so dotfiles, ~/.ssh and shell history stay out of
    reach. platformdirs resolves these per-OS (and honours XDG on Linux), so
    this is not a macOS-shaped guess.

    Downloads is in the list on purpose even though it is where untrusted files
    land — that is the case taint tracking exists for, and excluding it would
    just mean the assistant can't help with the folder people most often want
    help with. Reading one of those files still marks the conversation.
    """
    import platformdirs

    roots = []
    for getter in (
        platformdirs.user_documents_path,
        platformdirs.user_downloads_path,
        platformdirs.user_desktop_path,
    ):
        try:
            roots.append(getter())
        except Exception:  # noqa: BLE001 - a missing folder is not a startup failure
            continue
    return tuple(roots)


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

    tools = raw.get("tools", {})
    allow_dangerous = tools.get("allow_dangerous", True)
    if not isinstance(allow_dangerous, bool):
        raise ConfigError("CONFIG_INVALID_VALUE", "[tools] allow_dangerous must be a boolean")

    # Absent key ⇒ defaults; present-but-empty ⇒ deny all. The distinction is
    # the whole point, so `.get` with a sentinel rather than `.get(k, default)`.
    filesystem = raw.get("filesystem", {})
    roots_raw = filesystem.get("roots")
    if roots_raw is None:
        roots = default_filesystem_roots()
    elif isinstance(roots_raw, list) and all(isinstance(r, str) for r in roots_raw):
        roots = tuple(Path(r).expanduser() for r in roots_raw)
    else:
        raise ConfigError("CONFIG_INVALID_VALUE", "[filesystem] roots must be a list of strings")

    ddir = data_dir()
    ddir.mkdir(parents=True, exist_ok=True)
    return Config(
        ollama_url=ollama_url.rstrip("/"),
        default_model=default_model,
        wake_threshold=float(wake_threshold),
        allow_dangerous_tools=allow_dangerous,
        filesystem_roots=roots,
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
