"""Reader for the curated model catalog (catalog/models.toml).

The catalog is bundled *data*, never a service: refreshing it is a manual user
action, per docs/security-model.md §6. This module is the only thing that reads
it, so the file's location is a single-point concern.

Until now nothing loaded this file — it was written in phase 1 as the shape the
onboarding/settings model browser would render from. Phase 4 gives it its first
real job: an entry tagged `tool-calling` is what lets a model be handed a tool
schema by default (see capabilities.py for why that gate exists).

Failure is deliberately quiet and fail-safe. A missing or malformed catalog
yields an EMPTY curated set, which means every model falls back to "opt-in"
tool support — fewer tools enabled, never more.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CATALOG_FILENAME = "models.toml"
TOOL_CALLING_TAG = "tool-calling"
REASONING_TAG = "reasoning"


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    tier: int | None  # minimum RAM in GB for a good experience
    tags: tuple[str, ...]
    note: str

    @property
    def tool_calling(self) -> bool:
        return TOOL_CALLING_TAG in self.tags

    @property
    def reasoning(self) -> bool:
        """Emits a thinking pass before its answer — see reasoning_ids()."""
        return REASONING_TAG in self.tags


def catalog_path() -> Path | None:
    """Locate the bundled catalog in both dev and frozen layouts.

    PyInstaller onedir puts data files under sys._MEIPASS; from source the file
    lives at the repo root, three parents above this package. Returns None when
    the file genuinely isn't there — see the module docstring for why that is a
    survivable state rather than an error.
    """
    candidates = []
    if bundle := getattr(sys, "_MEIPASS", None):
        candidates.append(Path(bundle) / "catalog" / CATALOG_FILENAME)
    # llm/ -> jarvis_backend/ -> backend/ -> repo root
    candidates.append(Path(__file__).resolve().parents[3] / "catalog" / CATALOG_FILENAME)
    return next((p for p in candidates if p.is_file()), None)


@lru_cache(maxsize=1)
def entries() -> tuple[CatalogEntry, ...]:
    """Parsed catalog entries. Cached: the file is bundled data and cannot
    change under a running process."""
    path = catalog_path()
    if path is None:
        return ()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    out = []
    for m in raw.get("models", []):
        model_id = m.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue  # an unusable row is skipped, not fatal
        tags = m.get("tags", [])
        out.append(
            CatalogEntry(
                id=model_id,
                tier=m.get("tier") if isinstance(m.get("tier"), int) else None,
                tags=tuple(t for t in tags if isinstance(t, str)),
                note=m.get("note", "") if isinstance(m.get("note"), str) else "",
            )
        )
    return tuple(out)


@lru_cache(maxsize=1)
def tool_calling_ids() -> frozenset[str]:
    """Model ids curated as safe to hand a tool schema by default.

    Matching against these is EXACT, deliberately. `qwen3:4b` is a prefix of
    `qwen3:4b-thinking-2507`, which is a materially different model that nobody
    has measured — and this set gates a security-relevant default. An exotic
    quantisation is the user's to opt into per-model, not ours to assume.
    """
    return frozenset(e.id for e in entries() if e.tool_calling)


@lru_cache(maxsize=1)
def reasoning_ids() -> frozenset[str]:
    """Models that emit a thinking pass before answering.

    These are excluded from *auto*-selection (llm/tiering.pick_model), because
    the reasoning pass lands entirely before the first content token and the
    voice path budgets ~0.65 s for the whole LLM leg. Measured on the 8 GB M2:
    qwen3:4b takes 20 s to first content, and disabling thinking only stops
    Ollama SEPARATING it — the monologue then arrives in `content` and gets
    spoken aloud. docs/tool-calling.md has the numbers.

    A model the user configures explicitly is still honoured: this filters what
    we choose ON someone's behalf, not what they may choose.
    """
    return frozenset(e.id for e in entries() if e.reasoning)
