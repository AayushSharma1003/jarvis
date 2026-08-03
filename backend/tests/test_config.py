"""Config loading, focused on the settings that decide what tools may do.

The absent-versus-empty distinction for `[filesystem] roots` is the one worth
pinning: "the user hasn't chosen" and "the user chose nothing" look identical in
a naive `.get(key, default)` and mean opposite things. Getting it backwards
turns "I switched file access off" into "file access is on everywhere I keep my
documents".
"""

from __future__ import annotations

import os

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


# -- an unreadable config must not brick the app ----------------------------
#
# The README tells users their configuration IS this file ("No settings
# screen"), so a typo in it is an ordinary user action, not an edge case.
# Before `load_or_default` existed, one unclosed bracket raised out of
# `main.run()` and the sidecar exited rc=1 with no ready line — which the user
# sees only as "Backend didn't start in time", from an app that will now never
# start again and has no UI left to explain why. Reproduced on the shipped
# v0.1.0-rc6 binary, not theorised.
#
# The fallback direction is the whole design. Falling back to the *defaults*
# would be a security regression: a user whose file is `roots = ["~/safe"]`
# plus a typo three lines later would silently have their sandbox widened to
# Documents + Downloads + Desktop. So the security-bearing settings fail
# CLOSED — no roots, no dangerous tools — and only the harmless ones default.


def test_a_malformed_config_returns_defaults_instead_of_raising(config_file, tmp_path):
    from jarvis_backend.config import load_or_default

    (tmp_path / "config" / "config.toml").write_text('[filesystem]\nroots = ["~/x"\n')
    config, code = load_or_default()
    assert code == "CONFIG_PARSE_ERROR"
    assert config.ollama_url == "http://127.0.0.1:11434"


def test_a_malformed_config_switches_file_access_OFF_not_to_the_defaults(config_file, tmp_path):
    """The fail-safe direction. Widening the sandbox because we couldn't read
    the file that narrows it is the one outcome worse than refusing to start."""
    from jarvis_backend.config import load_or_default

    (tmp_path / "config" / "config.toml").write_text('[filesystem]\nroots = ["~/safe"\n')
    config, code = load_or_default()
    assert code == "CONFIG_PARSE_ERROR"
    assert config.filesystem_roots == (), "an unreadable config must not grant the defaults"
    assert config.allow_dangerous_tools is False, "nor may it leave dangerous tools on"


def test_an_invalid_value_is_reported_with_its_own_code(config_file, tmp_path):
    from jarvis_backend.config import load_or_default

    (tmp_path / "config" / "config.toml").write_text("[wake]\nthreshold = 9.0\n")
    config, code = load_or_default()
    assert code == "CONFIG_INVALID_VALUE"
    assert config.filesystem_roots == ()


def test_a_good_config_reports_no_error(config_file, tmp_path):
    from jarvis_backend.config import load_or_default

    (tmp_path / "config" / "config.toml").write_text("[tools]\nallow_dangerous = false\n")
    config, code = load_or_default()
    assert code is None
    assert config.allow_dangerous_tools is False
    assert config.filesystem_roots == default_filesystem_roots()


def test_a_real_sidecar_boots_on_a_malformed_config(tmp_path):
    """The test that would have caught the shipped bug, and the one the unit
    tests above cannot be: they prove `load_or_default` works, not that
    `main.run` calls it. That is gotcha 24's gap — "the function works" versus
    "the function runs" — and here it is the whole defect. Reverting main.py to
    a bare `load()` passes every other test in this file.

    So: a real process, a real malformed config, and the one output that
    matters — the ready line the Tauri shell waits for. Without it the user
    gets "Backend didn't start in time" and an app that never starts again.
    """
    import json
    import subprocess
    import sys

    cdir, ddir = tmp_path / "c", tmp_path / "d"
    cdir.mkdir()
    (cdir / "config.toml").write_text('[filesystem]\nroots = ["~/x"\n', encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, "-c", "from jarvis_backend.main import run; run()"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "JARVIS_CONFIG_DIR": str(cdir),
            "JARVIS_DATA_DIR": str(ddir),
            "JARVIS_WS_TOKEN": "t",
            "JARVIS_PORT": "0",
        },
    )
    try:
        line = proc.stdout.readline()
        assert line, f"no ready line; the sidecar died: {proc.stderr.read()[-600:]}"
        ready = json.loads(line)
        assert ready["event"] == "ready"
        assert ready["port"] > 0
    finally:
        proc.kill()
        proc.wait(timeout=10)
