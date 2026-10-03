"""workshop_mcp — MCP tools over the LoRA promote/audit pipeline."""

from __future__ import annotations

import difflib
import sys
from pathlib import Path

from fastmcp import FastMCP

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workshop import config, snapshot
from workshop.pipeline import inspect_pipeline, run_script
from workshop.scrape import lookup

WORKSHOP = config.WORKSHOP
SCRAPE = config.SCRAPE
PY = config.PYTHON
MANIFEST = config.MANIFEST
MATRIX = config.MATRIX
LEDGER = config.LEDGER
TAXONOMY = config.TAXONOMY
STATUS = config.STATUS
_IDX: lookup.Index | None = None

mcp = FastMCP("workshop")


def _idx(force: bool = False) -> lookup.Index:
    global _IDX
    content = lookup._inputs(lookup.MANIFEST, lookup.ap.TAXONOMY)
    fp = lookup._content_fingerprint(content)
    if force or _IDX is None or _IDX.fp != fp:
        _IDX = lookup.Index.load(
            lookup.MANIFEST,
            lookup.ap.TAXONOMY,
            lookup.INDEX,
            force=force,
            content=content,
        )
    return _IDX


@mcp.tool()
def who_uses(trigger: str) -> dict:
    """LoRAs using a trigger. Accepts alias, b5_ token, or (term:1.2)."""
    ix = _idx()
    cn = ix._canon(trigger)
    if cn is None:
        return {
            "canon": None,
            "known": False,
            "loras": [],
            "error": "ignored or undecodable",
        }
    return {
        "canon": cn,
        "category": ix.tax.canon_cat.get(cn, "uncategorized"),
        "known": ix.tax.known(cn),
        "loras": ix.by_canon.get(cn, []),
    }


@mcp.tool()
def triggers_of(lora_id: str) -> dict:
    """Canonical triggers for one LoRA, grouped by category."""
    ix = _idx()
    grouped: dict[str, list[str]] = {}
    for cn in ix.by_lora.get(lora_id, []):
        grouped.setdefault(ix.tax.canon_cat.get(cn, "uncategorized"), []).append(cn)
    return {"lora": lora_id, "found": lora_id in ix.by_lora, "triggers": grouped}


@mcp.tool()
def category_view(category: str) -> dict:
    """Every canonical tag in a category and the LoRAs using it."""
    ix = _idx()
    return {
        "category": category,
        "tags": {cn: ix.by_canon.get(cn, []) for cn in ix.by_cat.get(category, [])},
    }


@mcp.tool()
def suggest_trigger(term: str, n: int = 8) -> list[dict]:
    """Fuzzy matches against canon + aliases."""
    ix = _idx()
    q = lookup.ap.normalize(term)
    hits = difflib.get_close_matches(q, list(ix.tax.alias_to_canon), n=n, cutoff=0.6)
    seen, out = set(), []
    for h in hits:
        cn = ix.tax.alias_to_canon[h]
        if cn not in seen:
            seen.add(cn)
            out.append({"match": h, "canon": cn, "loras": len(ix.by_canon.get(cn, []))})
    return out


@mcp.tool()
def stack_check(loras: list[str], threshold: float = 0.5) -> dict:
    """Pre-flight collision check for a LoRA stack."""
    ix = _idx()
    missing = [l for l in loras if l not in ix.by_lora]
    matrix = ix.matrix if ix.matrix is not None else _load(MATRIX)
    pairs = []
    for i, a in enumerate(loras):
        sa = set(ix.by_lora.get(a, []))
        for b in loras[i + 1 :]:
            sb = set(ix.by_lora.get(b, []))
            shared = sa & sb
            if shared:
                j = round(len(shared) / len(sa | sb), 3)
                m = matrix.get("pairs", {}).get(
                    "|".join(sorted([a, b])), {"status": "untested"}
                )
                pairs.append(
                    {
                        "a": a,
                        "b": b,
                        "overlap": j,
                        "shared": sorted(shared),
                        "flag": j >= threshold,
                        "matrix": m.get("score", m.get("status")),
                    }
                )
    return {
        "ok": not missing and not any(p["flag"] for p in pairs),
        "missing": missing,
        "pairs": sorted(pairs, key=lambda p: -p["overlap"]),
    }


@mcp.tool()
def orphan_tags() -> list[str]:
    """Taxonomy entries no prod LoRA uses (prune candidates)."""
    ix = _idx()
    return sorted(cn for cn in ix.tax.canon_cat if cn not in ix.by_canon)


@mcp.tool()
def pipeline_status(rebuild_index: bool = False) -> dict:
    """Last scheduled run summary; optionally force an index rebuild."""
    if (SCRAPE.parent / "_vigiles_workshop.json").exists():
        from workshop.operations import pipeline_status as native_status

        state = native_status(SCRAPE.parent)
        if rebuild_index:
            if not state["readable"]:
                state["index_rebuild"] = "blocked_pending_recovery"
            else:
                try:
                    _idx(force=True)
                    state["index_rebuild"] = "completed"
                except (OSError, ValueError, TypeError) as exc:
                    state = native_status(SCRAPE.parent)
                    state["index_rebuild"] = "failed"
                    state["index_error"] = str(exc)
        return state
    if rebuild_index:
        _idx(force=True)
    return _load(STATUS) or {"status": "no run recorded"}


def _load(p: Path) -> dict:
    return snapshot.load_json(p)


def _alias_index() -> dict[str, tuple[str, str]]:
    """Build an index of alias -> (category, canonical_tag)."""
    tax = lookup.ap.Taxonomy.from_data(_load(TAXONOMY))
    return {
        alias: (tax.canon_cat[canon], canon)
        for alias, canon in tax.alias_to_canon.items()
    }


def _run(script: str, *args: str, timeout: int = 900) -> dict:
    return run_script(SCRAPE, PY, script, *args, timeout=timeout)


@mcp.tool(annotations={"readOnlyHint": True})
def pipeline_readiness() -> dict:
    """Inspect dependencies and schemas without executing external pipeline code."""
    return inspect_pipeline(SCRAPE, PY)


@mcp.tool()
def normalize_triggers(triggers: list[str]) -> dict:
    """Map raw Civitai trigger words to canonical taxonomy tags.
    Return any unknown triggers so the taxonomy can be extended."""
    idx = _alias_index()
    mapped: dict[str, set[str]] = {}
    unknown: list[str] = []
    for t in triggers:
        key = lookup.ap.normalize(t)
        if key in idx:
            cat, canon = idx[key]
            mapped.setdefault(cat, set()).add(canon)
        else:
            unknown.append(t)
    return {
        "tags": {k: sorted(v) for k, v in mapped.items()},
        "unknown": unknown,
    }


@mcp.tool()
def query_manifest(
    category: str | None = None,
    tag: str | None = None,
    base_model: str | None = None,
) -> list[dict]:
    """Filter promoted LoRAs by category, canonical tag, or base model."""
    out = []
    for name, e in _load(MANIFEST).get("loras", {}).items():
        if category and category not in e.get("categories", []):
            continue
        if tag and tag not in e.get("tags", []):
            continue
        if base_model and e.get("base_model") != base_model:
            continue
        out.append({**e, "name": name})
    return out


@mcp.tool()
def lookup_trigger_conflicts(lora_name: str) -> list[dict]:
    """Find promoted LoRAs whose canonical tags overlap with lora_name."""
    loras = _load(MANIFEST).get("loras", {})
    target = set(loras.get(lora_name, {}).get("tags", []))
    hits = []
    for name, e in loras.items():
        if name == lora_name:
            continue
        overlap = target & set(e.get("tags", []))
        if overlap:
            hits.append({"lora": name, "shared_tags": sorted(overlap)})
    return sorted(hits, key=lambda h: -len(h["shared_tags"]))


@mcp.tool()
def compatibility(lora_a: str, lora_b: str) -> dict:
    """Return the matrix score and notes for a LoRA pair."""
    m = _load(MATRIX)
    key = "|".join(sorted([lora_a, lora_b]))
    return m.get("pairs", {}).get(key, {"status": "untested", "pair": key})


@mcp.tool()
def lora_who(trigger: str) -> dict:
    """Which prod LoRAs use this trigger (alias/b5_/weighted ok)."""
    return _idx().who(trigger)


@mcp.tool()
def lora_triggers(lora_id: str) -> dict:
    """Canonical triggers of a LoRA plus which LoRAs share each."""
    return _idx().of(lora_id)


@mcp.tool()
def lora_category(category: str) -> dict:
    """All canonical terms in a category -> LoRAs."""
    return _idx().cat(category)


@mcp.tool()
def lora_suggest(term: str, n: int = 5) -> list[dict]:
    """Fuzzy canonical matches for a term."""
    return _idx().suggest(term, n)


@mcp.tool()
def lora_stack_check(lora_ids: list[str], threshold: float = 0.5) -> dict:
    """Pre-flight trigger collision check for a LoRA stack."""
    return _idx().stack(lora_ids, threshold)


@mcp.tool()
def lora_orphans() -> list[str]:
    """Taxonomy terms no prod LoRA uses."""
    return _idx().orphans()


@mcp.tool()
def promote_lora(candidate: str, dry_run: bool = True) -> dict:
    """Promote a sandbox-tested LoRA. Defaults to dry run."""
    args = ["--candidate", candidate] + (["--dry-run"] if dry_run else [])
    return _run("promote.py", *args)


@mcp.tool()
def quarantine_lora(name: str, reason: str, dry_run: bool = False) -> dict:
    """Move a LoRA to quarantine and log the reason to the ledger."""
    return _run(
        "promote.py",
        "--quarantine",
        name,
        "--reason",
        reason,
        *(["--dry-run"] if dry_run else []),
    )


@mcp.tool()
def prepare_promotion(candidate: str) -> dict:
    """Native: persist a reviewable promotion plan without publishing assets."""
    return _run("promote.py", "--prepare-candidate", candidate)


@mcp.tool()
def prepare_quarantine(name: str, reason: str) -> dict:
    """Native: persist a reviewable quarantine plan without moving assets."""
    return _run("promote.py", "--prepare-quarantine", name, "--reason", reason)


@mcp.tool(annotations={"readOnlyHint": True})
def list_plans(limit: int = 5, cursor: str | None = None) -> dict:
    """Native: discover paged plan metadata states without checking asset hashes."""
    return _run(
        "promote.py",
        "--list-plans",
        "--limit",
        str(limit),
        *(["--cursor", cursor] if cursor is not None else []),
    )


@mcp.tool(annotations={"readOnlyHint": True})
def inspect_plan(plan_id: str) -> dict:
    """Native: read the saved plan summary and current applicability."""
    return _run("promote.py", "--show-plan", plan_id)


@mcp.tool()
def apply_plan(plan_id: str, plan_sha256: str, dry_run: bool = True) -> dict:
    """Native: check or apply the exact reviewed digest; defaults to read-only."""
    return _run(
        "promote.py",
        "--apply-plan",
        plan_id,
        "--plan-sha256",
        plan_sha256,
        *(["--dry-run"] if dry_run else []),
    )


@mcp.tool()
def cancel_plan(plan_id: str, plan_sha256: str) -> dict:
    """Native: cancel an unused plan, retaining it as review evidence."""
    return _run("promote.py", "--cancel-plan", plan_id, "--plan-sha256", plan_sha256)


@mcp.tool()
def run_audit(prune: bool = False, apply: bool = False) -> dict:
    """Reconcile the manifest against the compatibility matrix."""
    return _run(
        "audit_matrix.py",
        *(["--prune"] if prune else []),
        *(["--apply"] if apply else []),
    )


@mcp.tool()
def recover_pipeline(operation_id: str, dry_run: bool = True) -> dict:
    """Preview or explicitly resume a native workshop operation journal."""
    return _run(
        "promote.py", "--recover", operation_id, *(["--dry-run"] if dry_run else [])
    )


@mcp.tool(annotations={"readOnlyHint": True})
def diagnose_recovery(operation_id: str) -> dict:
    """Native: explain recovery evidence and all control/asset conflicts without writes."""
    return _run("promote.py", "--diagnose-recovery", operation_id)


@mcp.tool()
def ledger_tail(n: int = 25) -> list[dict]:
    """Return the last n rows of _lora_ledger.csv."""
    return snapshot.ledger_tail(LEDGER, n)


if __name__ == "__main__":
    mcp.run()
