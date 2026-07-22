import pytest

from jarvis_backend.llm import catalog
from jarvis_backend.llm.base import LLMError, ModelInfo
from jarvis_backend.llm.tiering import pick_model, tier_budget_b


def _m(id, params=None):
    return ModelInfo(id=id, parameter_size=params)


@pytest.fixture
def reasoning(monkeypatch):
    """Declare which model ids the catalog tags as reasoning models."""

    def _set(*ids):
        monkeypatch.setattr(catalog, "reasoning_ids", lambda: frozenset(ids))
        # tiering imported the symbol directly.
        monkeypatch.setattr("jarvis_backend.llm.tiering.reasoning_ids", lambda: frozenset(ids))

    return _set


def test_tier_budgets():
    assert tier_budget_b(8) == 4.5
    assert tier_budget_b(16) == 9.0
    assert tier_budget_b(32) == 16.0
    assert tier_budget_b(64) > 16.0


def test_picks_largest_within_budget_on_8gb():
    models = [_m("llama3.2:3b", "3.2B"), _m("qwen2.5:7b", "7.6B")]
    assert pick_model(models, total_ram_gb=8) == "llama3.2:3b"


def test_picks_larger_on_16gb():
    models = [_m("llama3.2:3b", "3.2B"), _m("qwen2.5:7b", "7.6B")]
    assert pick_model(models, total_ram_gb=16) == "qwen2.5:7b"


def test_params_from_name_when_metadata_missing():
    assert pick_model([_m("phi3:14b"), _m("some:3b")], total_ram_gb=8) == "some:3b"


def test_all_over_budget_picks_smallest():
    models = [_m("huge:70b", "70B"), _m("big:14b", "14B")]
    assert pick_model(models, total_ram_gb=8) == "big:14b"


def test_configured_wins_and_must_exist():
    models = [_m("a:3b", "3B"), _m("b:7b", "7B")]
    assert pick_model(models, configured="b:7b", total_ram_gb=8) == "b:7b"
    with pytest.raises(LLMError) as e:
        pick_model(models, configured="missing:1b")
    assert e.value.code == "MODEL_NOT_FOUND"


def test_no_models():
    with pytest.raises(LLMError) as e:
        pick_model([])
    assert e.value.code == "NO_MODELS"


# -- reasoning models are skipped when choosing FOR the user ----------------
# A reasoning model generates its whole thinking pass before the first content
# token (qwen3:4b: 20s on the 8GB M2, vs a ~0.65s budget for the LLM leg), so
# auto-selecting one silently breaks the voice loop. docs/tool-calling.md.


def test_auto_select_skips_reasoning_model(reasoning):
    """The exact regression: pulling qwen3:4b made it the 8GB default purely
    because 4.0B > 3.2B, and voice went to ~20s before the first word."""
    reasoning("qwen3:4b")
    models = [_m("llama3.2:3b", "3.2B"), _m("qwen3:4b", "4B")]
    assert pick_model(models, total_ram_gb=8) == "llama3.2:3b"


def test_reasoning_model_still_wins_when_configured(reasoning):
    """Filtering applies to what we choose FOR the user, never what they ask
    for — text-mode tool work is a legitimate reason to want qwen3:4b."""
    reasoning("qwen3:4b")
    models = [_m("llama3.2:3b", "3.2B"), _m("qwen3:4b", "4B")]
    assert pick_model(models, configured="qwen3:4b", total_ram_gb=8) == "qwen3:4b"


def test_reasoning_model_used_when_it_is_the_only_option(reasoning):
    """A slow assistant beats NO_MODELS."""
    reasoning("qwen3:4b")
    assert pick_model([_m("qwen3:4b", "4B")], total_ram_gb=8) == "qwen3:4b"


def test_reasoning_skipped_in_the_over_budget_fallback(reasoning):
    """Nothing fits, so we take the smallest — but still not a reasoning one."""
    reasoning("qwen3:14b")
    models = [_m("qwen3:14b", "14B"), _m("plain:32b", "32B")]
    assert pick_model(models, total_ram_gb=8) == "plain:32b"


def test_non_reasoning_larger_model_still_preferred(reasoning):
    """The size rule is unchanged for everything that isn't a reasoning model."""
    reasoning("qwen3:4b")
    models = [_m("small:1b", "1B"), _m("mid:4b", "4B"), _m("qwen3:4b", "4B")]
    assert pick_model(models, total_ram_gb=8) == "mid:4b"
