"""workshop_mcp — MCP tools over the LoRA promote/audit pipeline."""

from __future__ import annotations

import csv
import difflib
import json
import subprocess
import sys
from pathlib import Path

from fastmcp import FastMCP

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workshop.scrape import lookup

ROOT = Path(r"D:\AI\ComfyUI_portable")
WORKSHOP = ROOT / "workshop"
SCRAPE = WORKSHOP / "scrape"
PY = ROOT / "python_embeded" / "python.exe"
MANIFEST = SCRAPE / "_lora_manifest.json"
MATRIX = SCRAPE / "_compatibility_matrix.json"
LEDGER = SCRAPE / "_lora_ledger.csv"
TAXONOMY = Path(__file__).parent / "taxonomy.json"
STATUS = SCRAPE / "_pipeline_status.json"
_IDX: lookup.Index | None = None

mcp = FastMCP("workshop")


def _idx(force: bool = False) -> lookup.Index:
    global _IDX
    fp = lookup._fingerprint(lookup.MANIFEST, lookup.ap.TAXONOMY)
    if force or _IDX is None or _IDX.fp != fp:
        _IDX = lookup.Index.load(force=force)
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
    pairs = []
    for i, a in enumerate(loras):
        sa = set(ix.by_lora.get(a, []))
        for b in loras[i + 1 :]:
            sb = set(ix.by_lora.get(b, []))
            shared = sa & sb
            if shared:
                j = round(len(shared) / len(sa | sb), 3)
                m = compatibility(a, b)
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
    if rebuild_index:
        _idx(force=True)
    return _load(STATUS) or {"status": "no run recorded"}


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _alias_index() -> dict[str, tuple[str, str]]:
    """Build an index of alias -> (category, canonical_tag)."""
    idx = {}
    for cat, tags in _load(TAXONOMY).get("categories", {}).items():
        for canon, aliases in tags.items():
            for a in aliases + [canon]:
                idx[a.lower().replace(" ", "_")] = (cat, canon)
    return idx


def _run(script: str, *args: str, timeout: int = 900) -> dict:
    """Run a pipeline script with the embedded Python."""
    r = subprocess.run(
        [str(PY), str(SCRAPE / script), *args],
        cwd=SCRAPE,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "returncode": r.returncode,
        "stdout": r.stdout[-8000:],
        "stderr": r.stderr[-4000:],
    }


@mcp.tool()
def normalize_triggers(triggers: list[str]) -> dict:
    """Map raw Civitai trigger words to canonical taxonomy tags.
    Return any unknown triggers so the taxonomy can be extended."""
    idx = _alias_index()
    mapped: dict[str, set[str]] = {}
    unknown: list[str] = []
    for t in triggers:
        key = t.strip().lower().replace(" ", "_")
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
def quarantine_lora(name: str, reason: str) -> dict:
    """Move a LoRA to quarantine and log the reason to the ledger."""
    return _run("promote.py", "--quarantine", name, "--reason", reason)


@mcp.tool()
def run_audit(prune: bool = False) -> dict:
    """Reconcile the manifest against the compatibility matrix."""
    return _run("audit_matrix.py", *(["--prune"] if prune else []))


@mcp.tool()
def ledger_tail(n: int = 25) -> list[dict]:
    """Return the last n rows of _lora_ledger.csv."""
    if not LEDGER.exists():
        return []
    with LEDGER.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))[-n:]


if __name__ == "__main__":
    mcp.run()
