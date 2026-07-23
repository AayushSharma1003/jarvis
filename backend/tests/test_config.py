"""Config loading, focused on the settings that decide what tools may do.

The absent-versus-empty distinction for `[filesystem] roots` is the one worth
pinning: "the user hasn't chosen" and "the user chose nothing" look identical in
a naive `.get(key, default)` and mean opposite things. Getting it backwards
turns "I switched file access off" into "file access is on everywhere I keep my
documents".
"""

from __future__ import annotations

import pytest

from jarvis_backend.config import ConfigError, default_filesystem_roots, load


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    cdir = tmp_path / "config"
    cdir.mkdir()
    monkeypatch.setenv("JARVIS_CONFIG_DIR", str(cdir))
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))

    def write(body: str):
        (cdir / "config.toml").write_text(body, encoding="utf-8")
        return load()

    return write


# -- filesystem roots -------------------------------------------------------


def test_absent_roots_means_the_defaults(config_file):
    config = config_file("[llm]\nollama_url = 'http://x'\n")
    assert config.filesystem_roots == default_filesystem_roots()
    assert config.filesystem_roots, "the defaults must not be empty"


def test_an_explicit_empty_list_means_no_file_access(config_file):
    """NOT the defaults. This is a user switching file tools off, and it has to
    survive being indistinguishable-looking from an absent key."""
    config = config_file("[filesystem]\nroots = []\n")
    assert config.filesystem_roots == ()


def test_roots_are_user_expanded(config_file):
    config = config_file("[filesystem]\nroots = ['~/Somewhere']\n")
    assert len(config.filesystem_roots) == 1
    assert "~" not in str(config.filesystem_roots[0])


def test_the_generated_default_config_leaves_roots_absent(config_file, tmp_path):
    """The commented-out key in DEFAULT_CONFIG keeps one source of truth for
    the defaults — in code — while still showing the user the knob."""
    config = load()  # no file yet: load() writes DEFAULT_CONFIG
    assert config.filesystem_roots == default_filesystem_roots()
    assert "# roots = " in config.config_path.read_text()


@pytest.mark.parametrize("value", ["'~/Documents'", "[1, 2]", "true"])
def test_malformed_roots_are_rejected_loudly(config_file, value):
    """A typo must not silently become "no roots" (tools mysteriously broken)
    or "all roots" (much worse)."""
    with pytest.raises(ConfigError) as e:
        config_file(f"[filesystem]\nroots = {value}\n")
    assert e.value.code == "CONFIG_INVALID_VALUE"


def test_the_defaults_exclude_the_home_directory_itself():
    """Documents/Downloads/Desktop, not ~. Dotfiles, ~/.ssh and shell history
    stay out of reach on day one."""
    roots = default_filesystem_roots()
    assert roots
    home = roots[0].home()
    assert home not in roots
    assert all(r != home for r in roots)


# -- dangerous tools --------------------------------------------------------


def test_allow_dangerous_defaults_on(config_file):
    assert config_file("[llm]\nollama_url = 'http://x'\n").allow_dangerous_tools is True


def test_allow_dangerous_can_be_switched_off(config_file):
    assert config_file("[tools]\nallow_dangerous = false\n").allow_dangerous_tools is False


def test_a_non_boolean_allow_dangerous_is_rejected(config_file):
    with pytest.raises(ConfigError) as e:
        config_file("[tools]\nallow_dangerous = 'yes'\n")
    assert e.value.code == "CONFIG_INVALID_VALUE"
