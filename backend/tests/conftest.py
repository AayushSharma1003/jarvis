from __future__ import annotations

import pytest

from jarvis_backend.storage import db
from jarvis_backend.storage.conversations import Store


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Never let tests touch the real config/data dirs."""
    monkeypatch.setenv("JARVIS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture
def store():
    return Store(db.connect(":memory:"))
