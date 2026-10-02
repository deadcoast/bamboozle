"""workshop_mcp — MCP tools over the LoRA promote/audit pipeline."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from fastmcp import FastMCP

ROOT = Path(r"D:\AI\ComfyUI_portable")
WORKSHOP = ROOT / "workshop"
SCRAPE = WORKSHOP / "scrape"
PY = ROOT / "python_embeded" / "python.exe"
MANIFEST = SCRAPE / "_lora_manifest.json"
MATRIX = SCRAPE / "_compatibility_matrix.json"
LEDGER = SCRAPE / "_lora_ledger.csv"
TAXONOMY = Path(__file__).parent / "taxonomy.json"

mcp = FastMCP("workshop")


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
        cwd=SCRAPE, capture_output=True, text=True, timeout=timeout,
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
    mapped, unknown = {}, []
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
        out.append({"name": name, e})
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