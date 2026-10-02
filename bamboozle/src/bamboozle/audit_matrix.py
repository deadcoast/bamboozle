"""Canonical trigger overlap for an existing compatibility matrix."""

from pathlib import Path
from typing import Any

from workshop.scrape.alias_pass import Taxonomy, load_prod_triggers, pairwise_overlap
from workshop.scrape.alias_pass import run as alias_run


def update_trigger_overlap(
    matrix: dict[str, dict[str, Any]],
    manifest: Path,
    tax: Taxonomy | None = None,
    threshold: float = 0.5,
) -> dict[str, dict[str, Any]]:
    """Update pair entries in place, preserving existing scores and notes."""
    alias_rep = alias_run(load_prod_triggers(manifest), tax)
    for (a, b), overlap in pairwise_overlap(alias_rep).items():
        entry = matrix.setdefault(f"{a}|{b}", {})
        entry["trigger_overlap"] = overlap
        entry["trigger_conflict"] = overlap >= threshold
    return matrix
