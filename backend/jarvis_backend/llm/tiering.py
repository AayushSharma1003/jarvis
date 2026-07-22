"""RAM-tier model auto-selection.

Policy (docs/architecture.md): pick the largest installed model whose parameter
count fits this machine's tier budget. We never download models silently — if
nothing is installed, the caller surfaces NO_MODELS and onboarding handles it.
"""

from __future__ import annotations

import re

import psutil

from .base import LLMError, ModelInfo

# Max model parameter count (in billions) we consider comfortable per RAM tier.
# Conservative on purpose: the voice pipeline + webview need headroom too.
_TIER_BUDGET_B = (
    (9, 4.5),    # <=9 GB RAM  -> up to ~4B params
    (20, 9.0),   # <=20 GB     -> up to ~8B
    (40, 16.0),  # <=40 GB     -> up to ~14B
)
_MAX_BUDGET_B = 75.0

_PARAM_RE = re.compile(r"([\d.]+)\s*([MB])", re.IGNORECASE)


def ram_gb() -> float:
    return psutil.virtual_memory().total / 1024**3


def tier_budget_b(total_ram_gb: float | None = None) -> float:
    gb = ram_gb() if total_ram_gb is None else total_ram_gb
    for limit, budget in _TIER_BUDGET_B:
        if gb <= limit:
            return budget
    return _MAX_BUDGET_B


def params_b(model: ModelInfo) -> float | None:
    """Parameter count in billions, from runtime metadata or the model name."""
    for source in (model.parameter_size, model.id):
        if not source:
            continue
        m = _PARAM_RE.search(source)
        if m:
            value = float(m.group(1))
            return value / 1000 if m.group(2).upper() == "M" else value
    return None


def pick_model(
    models: list[ModelInfo], configured: str = "", total_ram_gb: float | None = None
) -> str:
    """Explicit config wins; otherwise the largest installed model within budget."""
    if not models:
        raise LLMError("NO_MODELS")
    ids = {m.id for m in models}
    if configured:
        if configured in ids:
            return configured
        raise LLMError("MODEL_NOT_FOUND", configured)

    budget = tier_budget_b(total_ram_gb)
    in_budget = [(p, m.id) for m in models if (p := params_b(m)) is not None and p <= budget]
    if in_budget:
        return max(in_budget)[1]
    # Everything is over budget (or unparseable): smallest known, else first.
    known = [(p, m.id) for m in models if (p := params_b(m)) is not None]
    return min(known)[1] if known else models[0].id
