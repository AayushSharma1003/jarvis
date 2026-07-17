import pytest

from jarvis_backend.llm.base import LLMError, ModelInfo
from jarvis_backend.llm.tiering import pick_model, tier_budget_b


def _m(id, params=None):
    return ModelInfo(id=id, parameter_size=params)


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
