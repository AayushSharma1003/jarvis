"""The tool-capability gate: which models may be handed a tool schema.

The gate is a security control, not a quality nicety (llm/capabilities.py has
the reasoning and the measurements), so these tests care most about the
fail-safe directions: unknown must never become "on", and a runtime hiccup must
never become a hard "unsupported".
"""

from __future__ import annotations

import httpx
import pytest

from jarvis_backend.llm import capabilities as caps
from jarvis_backend.llm import catalog
from jarvis_backend.llm.base import ChatBackend, ModelInfo
from jarvis_backend.llm.ollama import OllamaBackend


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    """entries()/tool_calling_ids() are lru_cached — the catalog is immutable
    bundled data in production, but tests swap it out."""
    catalog.entries.cache_clear()
    catalog.tool_calling_ids.cache_clear()
    yield
    catalog.entries.cache_clear()
    catalog.tool_calling_ids.cache_clear()


@pytest.fixture
def fake_catalog(tmp_path, monkeypatch):
    def _write(body: str):
        p = tmp_path / "models.toml"
        p.write_text(body, encoding="utf-8")
        monkeypatch.setattr(catalog, "catalog_path", lambda: p)
        catalog.entries.cache_clear()
        catalog.tool_calling_ids.cache_clear()
        return p

    return _write


CURATED = """
[[models]]
id = "qwen3:4b"
tier = 8
tags = ["chat", "tool-calling", "small"]
note = "Default on 8GB machines"

[[models]]
id = "plainchat:1b"
tier = 4
tags = ["chat"]
note = "No tool training"
"""


# -- classify ---------------------------------------------------------------


def test_curated_model_with_tools_capability_is_on(fake_catalog):
    fake_catalog(CURATED)
    assert caps.classify("qwen3:4b", ["completion", "tools"]) == caps.ON


def test_uncurated_model_is_optin_not_on(fake_catalog):
    """llama3.2:3b's real-world state: the template supports tools, but it
    scored 76% and cannot decline one, so it must not be on by default."""
    fake_catalog(CURATED)
    assert caps.classify("llama3.2:3b", ["completion", "tools"]) == caps.OPTIN


def test_curated_model_without_tools_capability_is_unsupported(fake_catalog):
    """The runtime's hard no outranks the catalog: handing tools to a template
    that can't express them produces garbage, curated or not."""
    fake_catalog(CURATED)
    assert caps.classify("qwen3:4b", ["completion"]) == caps.UNSUPPORTED


def test_unknown_capabilities_fall_through_to_catalog(fake_catalog):
    """None means "the backend can't say" — a cloud adapter, an old runtime, a
    failed probe. It must NOT be read as unsupported."""
    fake_catalog(CURATED)
    assert caps.classify("qwen3:4b", None) == caps.ON
    assert caps.classify("llama3.2:3b", None) == caps.OPTIN


def test_catalog_match_is_exact_not_prefix(fake_catalog):
    """`qwen3:4b` is a prefix of `qwen3:4b-thinking-2507`, a different model
    nobody has measured. Prefix matching would silently bless it."""
    fake_catalog(CURATED)
    assert caps.classify("qwen3:4b-thinking-2507", ["tools"]) == caps.OPTIN


def test_uncurated_model_without_tools_is_unsupported(fake_catalog):
    fake_catalog(CURATED)
    assert caps.classify("plainchat:1b", ["completion"]) == caps.UNSUPPORTED


# -- catalog loading --------------------------------------------------------


def test_missing_catalog_yields_no_curated_models(monkeypatch):
    """Fail-safe: no catalog means everything is opt-in (fewer tools), never
    everything on."""
    monkeypatch.setattr(catalog, "catalog_path", lambda: None)
    catalog.entries.cache_clear()
    catalog.tool_calling_ids.cache_clear()
    assert catalog.tool_calling_ids() == frozenset()
    assert caps.classify("qwen3:4b", ["tools"]) == caps.OPTIN


def test_malformed_catalog_is_survivable(fake_catalog):
    fake_catalog("this is not valid toml [[[")
    assert catalog.tool_calling_ids() == frozenset()


def test_catalog_skips_unusable_rows(fake_catalog):
    fake_catalog(
        """
[[models]]
tags = ["tool-calling"]

[[models]]
id = "good:4b"
tags = ["tool-calling"]
"""
    )
    assert catalog.tool_calling_ids() == frozenset({"good:4b"})


def test_bundled_catalog_is_loadable_and_curates_tools():
    """The real catalog/models.toml ships with the app and must parse. It is
    also the gate's only source of "on" — an empty one silently disables tools
    for everybody."""
    assert catalog.catalog_path() is not None, "bundled catalog not found"
    assert catalog.tool_calling_ids(), "bundled catalog curates no tool-calling models"


# -- resolve ----------------------------------------------------------------


class _Backend(ChatBackend):
    name = "fake"

    def __init__(self, caps_by_model):
        self._caps = caps_by_model
        self.probes: list[str] = []

    async def list_models(self):
        return [ModelInfo(id=i) for i in self._caps]

    async def stream_chat(self, model, messages):  # pragma: no cover - unused
        yield ""

    async def model_capabilities(self, model):
        self.probes.append(model)
        value = self._caps[model]
        if isinstance(value, Exception):
            raise value
        return value


async def test_resolve_classifies_every_model(fake_catalog):
    fake_catalog(CURATED)
    backend = _Backend(
        {
            "qwen3:4b": ["completion", "tools"],
            "llama3.2:3b": ["completion", "tools"],
            "plainchat:1b": ["completion"],
        }
    )
    got = await caps.resolve(backend, await backend.list_models())
    assert got == {
        "qwen3:4b": caps.ON,
        "llama3.2:3b": caps.OPTIN,
        "plainchat:1b": caps.UNSUPPORTED,
    }


async def test_resolve_survives_a_failing_probe(fake_catalog):
    """One broken probe must not fail models.list, and must not demote the
    model to a hard "unsupported" — it lands in the catalog-decided state."""
    fake_catalog(CURATED)
    backend = _Backend({"qwen3:4b": RuntimeError("boom"), "llama3.2:3b": ["tools"]})
    got = await caps.resolve(backend, await backend.list_models())
    assert got == {"qwen3:4b": caps.ON, "llama3.2:3b": caps.OPTIN}


# -- the Ollama probe itself ------------------------------------------------


def _ollama(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaBackend("http://test", client=client)


async def test_ollama_reports_capabilities():
    def handler(request):
        assert request.url.path == "/api/show"
        return httpx.Response(200, json={"capabilities": ["completion", "tools"]})

    assert await _ollama(handler).model_capabilities("m") == ["completion", "tools"]


async def test_ollama_capabilities_are_cached():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"capabilities": ["tools"]})

    b = _ollama(handler)
    await b.model_capabilities("m")
    await b.model_capabilities("m")
    assert len(calls) == 1, "models.list runs on every connect; this must not re-probe"


async def test_ollama_probe_failure_is_unknown_and_not_cached():
    """A transient failure must not pin the model into the wrong state for the
    life of the process."""
    responses = [
        httpx.ConnectError("refused"),
        httpx.Response(200, json={"capabilities": ["tools"]}),
    ]

    def handler(request):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    b = _ollama(handler)
    assert await b.model_capabilities("m") is None
    assert await b.model_capabilities("m") == ["tools"]


async def test_ollama_missing_capabilities_field_is_unknown():
    """Older Ollama builds have no `capabilities` key — unknown, not a hard no."""
    b = _ollama(lambda r: httpx.Response(200, json={"template": "..."}))
    assert await b.model_capabilities("m") is None
