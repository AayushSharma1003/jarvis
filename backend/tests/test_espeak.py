"""espeak-ng's data path length limit, and the copy that works around it.

espeak-ng copies the data path into a fixed-size buffer and calls exit() when
it does not fit. It does not raise, does not return an error code, and cannot
be caught -- the whole sidecar dies the first time Jarvis is asked to speak.
So the length has to be checked *before* the path is handed over, which is
what tts/espeak.py does.

Measured on the 8GB M2 (real directories, not symlinks -- phonemizer resolves
symlinks before espeak sees them, which hides the bug): 151 characters works,
152 exits with rc=1.
"""

from __future__ import annotations

import pytest

from jarvis_backend.tts.base import TTSError
from jarvis_backend.tts.espeak import MAX_DATA_PATH_CHARS, usable_data_path


def _make_data_dir(root, name: str):
    """A stand-in for espeak-ng-data: a directory with a recognisable file."""
    d = root / name
    d.mkdir(parents=True)
    (d / "phontab").write_text("stub")
    (d / "en_dict").write_text("stub")
    return d


def test_a_short_bundled_path_is_used_as_is(tmp_path):
    """The common case -- /Applications is 107 chars -- must not copy anything."""
    bundled = _make_data_dir(tmp_path, "espeak-ng-data")
    short_root = tmp_path / "data"
    short_root.mkdir()

    assert usable_data_path(str(bundled), short_root) is None
    assert not (short_root / "espeak-ng-data").exists()


def test_a_path_at_the_limit_is_still_used_as_is(tmp_path):
    """151 characters is measured-good; treating it as bad would copy for nothing."""
    padding = MAX_DATA_PATH_CHARS - len(str(tmp_path)) - len("/espeak-ng-data") - 1
    bundled = _make_data_dir(tmp_path / ("p" * padding), "espeak-ng-data")
    assert len(str(bundled)) == MAX_DATA_PATH_CHARS

    short_root = tmp_path / "d"
    short_root.mkdir()
    assert usable_data_path(str(bundled), short_root) is None


def test_a_path_one_over_the_limit_is_copied_somewhere_short(tmp_path):
    """152 characters is measured-fatal, so this is the case that must copy."""
    padding = MAX_DATA_PATH_CHARS - len(str(tmp_path)) - len("/espeak-ng-data")
    bundled = _make_data_dir(tmp_path / ("p" * padding), "espeak-ng-data")
    assert len(str(bundled)) == MAX_DATA_PATH_CHARS + 1

    short_root = tmp_path / "d"
    short_root.mkdir()

    result = usable_data_path(str(bundled), short_root)

    assert result == str(short_root / "espeak-ng-data")
    assert len(result) <= MAX_DATA_PATH_CHARS
    # The copy has to be a real one: a symlink would resolve back to the long
    # path before espeak ever sees it, which is exactly how this bug hides.
    copied = short_root / "espeak-ng-data"
    assert not copied.is_symlink()
    assert (copied / "phontab").read_text() == "stub"
    assert (copied / "en_dict").read_text() == "stub"


def test_an_existing_copy_is_reused_rather_than_recopied(tmp_path):
    """20MB per launch would be a real cost on the 8GB target."""
    padding = MAX_DATA_PATH_CHARS - len(str(tmp_path)) - len("/espeak-ng-data")
    bundled = _make_data_dir(tmp_path / ("p" * padding), "espeak-ng-data")
    short_root = tmp_path / "d"
    short_root.mkdir()

    first = usable_data_path(str(bundled), short_root)
    # A marker that a second copy would overwrite.
    marker = short_root / "espeak-ng-data" / "phontab"
    marker.write_text("not-recopied")

    second = usable_data_path(str(bundled), short_root)

    assert second == first
    assert marker.read_text() == "not-recopied"


def test_a_short_root_that_is_itself_too_long_raises_rather_than_exiting(tmp_path):
    """The one case with no way out must be an exception, never a silent death.

    Handing espeak a path we already know is too long is the process-exit bug
    with extra steps, so this refuses instead -- TTS is lost, the sidecar is
    not.
    """
    padding = MAX_DATA_PATH_CHARS - len(str(tmp_path)) - len("/espeak-ng-data")
    bundled = _make_data_dir(tmp_path / ("p" * padding), "espeak-ng-data")
    too_long_root = tmp_path / ("q" * padding)
    too_long_root.mkdir()

    with pytest.raises(TTSError) as excinfo:
        usable_data_path(str(bundled), too_long_root)

    assert excinfo.value.code == "TTS_ESPEAK_PATH_TOO_LONG"


def test_a_missing_bundled_path_is_not_treated_as_short(tmp_path):
    """A absent directory means something else is wrong; do not copy nothing."""
    short_root = tmp_path / "d"
    short_root.mkdir()
    missing = tmp_path / "nope" / "espeak-ng-data"

    with pytest.raises(TTSError) as excinfo:
        usable_data_path(str(missing), short_root)

    assert excinfo.value.code == "TTS_ESPEAK_DATA_MISSING"
