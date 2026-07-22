"""`system.readiness` — the first-run gate the UI shows when the machine can't
hold a conversation yet. Codes only: no English crosses the WS boundary."""

from __future__ import annotations

import pytest

from jarvis_backend import assets
from jarvis_backend.llm.base import LLMError, ModelInfo
from tests.test_ws import (  # noqa: F401 - fixtures
    FakeBackend,
    connect,
    curated,
    make_client,
)


class UnreachableBackend(FakeBackend):
    async def list_models(self):
        raise LLMError("OLLAMA_UNREACHABLE", "connection refused")


class EmptyBackend(FakeBackend):
    async def list_models(self):
        return []


class BigModelBackend(FakeBackend):
    async def list_models(self):
        return [ModelInfo(id="fake:3b", parameter_size="3B"), ModelInfo(id="huge:70b")]


@pytest.fixture(autouse=True)
def _models_present(monkeypatch):
    """Default to "assets installed" so tests don't depend on the dev machine."""
    monkeypatch.setattr(assets, "missing", lambda group=None: [])


def _ask(client):
    with connect(client) as ws:
        ws.send_json({"type": "system.readiness"})
        return ws.receive_json()


def _by_id(msg):
    return {c["id"]: c for c in msg["checks"]}


def test_readiness_is_ready_with_a_backend_and_a_model(make_client):  # noqa: F811
    client, _ = make_client()
    msg = _ask(client)
    assert msg["type"] == "readiness"
    assert msg["ready"] is True
    checks = _by_id(msg)
    assert checks["llm"]["status"] == "ok"
    assert checks["model"]["status"] == "ok"
    assert checks["model"]["data"]["model"] == "fake:3b"
    assert checks["model"]["data"]["source"] == "auto"
    # The tier travels with it, so the UI can say why this model.
    assert checks["model"]["data"]["params_b"] == 3.0
    assert checks["model"]["data"]["budget_b"] > 0


def test_unreachable_backend_blocks_readiness(make_client):  # noqa: F811
    client, _ = make_client(UnreachableBackend())
    msg = _ask(client)
    assert msg["ready"] is False
    llm = _by_id(msg)["llm"]
    assert (llm["status"], llm["code"]) == ("fail", "OLLAMA_UNREACHABLE")
    assert "model" not in _by_id(msg)  # nothing to pick from


def test_no_models_blocks_readiness(make_client):  # noqa: F811
    client, _ = make_client(EmptyBackend())
    msg = _ask(client)
    assert msg["ready"] is False
    checks = _by_id(msg)
    assert checks["llm"]["status"] == "ok"  # Ollama is running, it's just empty
    assert (checks["model"]["status"], checks["model"]["code"]) == ("fail", "NO_MODELS")


def test_missing_voice_models_warn_but_do_not_block(make_client, monkeypatch):  # noqa: F811
    absent = [assets.ASSETS["whisper-base"]]
    monkeypatch.setattr(assets, "missing", lambda group=None: absent if group == "voice" else [])
    client, _ = make_client()
    msg = _ask(client)
    # Text chat works without a voice pipeline, so this must not gate the app.
    assert msg["ready"] is True
    voice = _by_id(msg)["voice_models"]
    assert (voice["status"], voice["code"]) == ("warn", "VOICE_MODELS_MISSING")
    assert voice["data"]["models"] == ["whisper-base"]
    assert _by_id(msg)["wake_models"]["status"] == "ok"


def test_microphone_check_is_never_fatal(make_client):  # noqa: F811
    # Real hardware query: present on a dev Mac, absent in CI. Either way the
    # worst it may do is warn — a missing mic must not block text chat.
    client, _ = make_client()
    assert _by_id(_ask(client))["microphone"]["status"] in ("ok", "warn")


def test_models_list_carries_the_ram_tier(make_client):  # noqa: F811
    client, _ = make_client(BigModelBackend())
    with connect(client) as ws:
        ws.send_json({"type": "models.list"})
        msg = ws.receive_json()

    assert msg["tier"]["ram_gb"] > 0
    assert msg["source"] == "auto"
    by_id = {m["id"]: m for m in msg["models"]}
    assert by_id["fake:3b"]["params_b"] == 3.0
    assert by_id["fake:3b"]["over_budget"] is False
    # 70B is over budget on any machine this project targets.
    assert by_id["huge:70b"]["params_b"] == 70.0
    assert by_id["huge:70b"]["over_budget"] is True
    assert msg["default"] == "fake:3b"


# -- the tools row (deferred from M4.0, due with the permission engine) ------


def _tools_row(client):
    return _by_id(_ask(client))["tools"]


def test_tools_row_is_ok_for_a_curated_model(make_client, curated):  # noqa: F811
    from jarvis_backend.security.permissions import SafeOnlyGate
    from jarvis_backend.tools import default_registry

    client, _ = make_client(registry=default_registry(SafeOnlyGate()))
    row = _tools_row(client)
    assert row["status"] == "ok"
    assert row["data"]["model"] == "fake:3b"


def test_tools_row_warns_for_an_unvetted_model(make_client):  # noqa: F811
    """No `curated` fixture: fake:3b isn't in the catalog, so tools are off by
    default and the gate should say why rather than leaving the user guessing
    at a model that quietly can't do anything."""
    from jarvis_backend.security.permissions import SafeOnlyGate
    from jarvis_backend.tools import default_registry

    client, _ = make_client(registry=default_registry(SafeOnlyGate()))
    row = _tools_row(client)
    assert row["status"] == "warn"
    assert row["code"] == "TOOLS_OPTIN"


def test_tools_row_never_blocks_the_gate(make_client):  # noqa: F811
    """Tools being off costs the user actions, not conversation — exactly the
    reasoning that keeps missing voice models a warning."""
    from jarvis_backend.security.permissions import SafeOnlyGate
    from jarvis_backend.tools import default_registry

    client, _ = make_client(registry=default_registry(SafeOnlyGate()))
    msg = _ask(client)
    assert _by_id(msg)["tools"]["status"] == "warn"
    assert msg["ready"] is True


def test_tools_row_reports_a_hard_no_separately(make_client):  # noqa: F811
    """A model whose template has no tool support cannot be opted into, so it
    gets its own code — switching models is the only fix."""
    from jarvis_backend.security.permissions import SafeOnlyGate
    from jarvis_backend.tools import default_registry

    class NoToolsBackend(FakeBackend):
        async def model_capabilities(self, model):
            return ["completion"]

    client, _ = make_client(NoToolsBackend(), registry=default_registry(SafeOnlyGate()))
    row = _tools_row(client)
    assert row["status"] == "warn"
    assert row["code"] == "TOOLS_UNSUPPORTED"
