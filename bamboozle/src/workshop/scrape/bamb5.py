"""workshop_mcp — MCP tools over the LoRA promote/audit pipeline."""

# bamb5.py
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from fastmcp import FastMCP

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bamboozle.utils import atomic_write, json_bytes, parse_json, read_bytes, sha256
from workshop import config, snapshot
from workshop.pipeline import inspect_pipeline, run_script

ROOT = config.SCRAPE
KEY_PATH = config.KEY
PREFIX = "b5_"
AD = b"bamb5-v1"
WORKSHOP = config.WORKSHOP
SCRAPE = config.SCRAPE
PY = config.PYTHON
MANIFEST = config.MANIFEST
MATRIX = config.MATRIX
LEDGER = config.LEDGER
TAXONOMY = config.TAXONOMY

mcp = FastMCP("workshop")


# ---------- key ----------
def init_key(force: bool = False) -> None:
    if KEY_PATH.exists() and not force:
        raise FileExistsError(
            f"Key exists: {KEY_PATH} (use --force to rotate with a backup)"
        )
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists():
        previous = KEY_PATH.read_bytes()
        backup = KEY_PATH.with_name(f"{KEY_PATH.name}.{sha256(previous)[:16]}.bak")
        if not backup.exists():
            atomic_write(backup, previous)
        elif backup.read_bytes() != previous:
            raise ValueError("Key backup does not match; refusing rotation")
    atomic_write(KEY_PATH, AESSIV.generate_key(512), overwrite=force)
    print(f"Wrote {KEY_PATH}")


def _cipher() -> AESSIV:
    if not KEY_PATH.exists():
        raise FileNotFoundError("No key. Run: python -m workshop.scrape.bamb5 init")
    return AESSIV(read_bytes(KEY_PATH, 64))


def _load(p: Path) -> dict:
    return snapshot.load_json(p)


def _alias_index() -> dict[str, tuple[str, str]]:
    """Build an index of alias -> (category, canonical_tag)."""
    from workshop.scrape.alias_pass import Taxonomy

    tax = Taxonomy.from_data(_load(TAXONOMY))
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
    from workshop.scrape.alias_pass import normalize

    idx = _alias_index()
    mapped: dict[str, set[str]] = {}
    unknown: list[str] = []
    for t in triggers:
        key = normalize(t)
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


@mcp.tool()
def ledger_tail(n: int = 25) -> list[dict]:
    """Return the last n rows of _lora_ledger.csv."""
    return snapshot.ledger_tail(LEDGER, n)


# ---------- core ----------
def _b32e(b: bytes) -> str:
    return base64.b32encode(b).decode().rstrip("=").lower()


def _b32d(s: str) -> bytes:
    s = s.upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def encode(term: str, c: AESSIV | None = None) -> str:
    if term.startswith(PREFIX):
        decode(term, c)
        return term  # idempotent
    c = c or _cipher()
    return PREFIX + _b32e(c.encrypt(term.encode("utf-8"), [AD]))


def decode(token: str, c: AESSIV | None = None) -> str:
    if not token.startswith(PREFIX):
        return token  # passthrough plaintext
    c = c or _cipher()
    return c.decrypt(_b32d(token[len(PREFIX) :]), [AD]).decode("utf-8")


def encode_list(terms, c=None):
    c = c or _cipher()
    return [encode(t, c) for t in terms]


def decode_list(tokens, c=None):
    c = c or _cipher()
    return [decode(t, c) for t in tokens]


# ---------- bulk JSON ----------
def _walk(obj, keys, fn):
    if isinstance(obj, dict):
        return {
            k: (_apply(v, fn) if k in keys else _walk(v, keys, fn))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_walk(v, keys, fn) for v in obj]
    return obj


def _apply(v, fn):
    if isinstance(v, str):
        return fn(v)
    if isinstance(v, list):
        return [fn(x) if isinstance(x, str) else x for x in v]
    return v


def process_json(path: Path, keys: set[str], mode: str) -> None:
    if mode not in {"encode", "decode"}:
        raise ValueError("JSON mode must be encode or decode")
    c = _cipher()
    fn = (lambda s: encode(s, c)) if mode == "encode" else (lambda s: decode(s, c))
    data = parse_json(read_bytes(path))
    out = _walk(data, keys, fn)
    atomic_write(path, json_bytes(out), overwrite=True)
    print(f"{mode}d {path} keys={sorted(keys)}")


# ---------- CLI ----------
def main():
    p = argparse.ArgumentParser(prog="bamb5")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--force", action="store_true")
    for name in ("enc", "dec"):
        s = sub.add_parser(name)
        s.add_argument("terms", nargs="+")
    for name in ("enc-json", "dec-json"):
        s = sub.add_parser(name)
        s.add_argument("file", type=Path)
        s.add_argument("--keys", default="triggers,tags,trained_words")
    a = p.parse_args()

    if a.cmd == "init":
        init_key(a.force)
    elif a.cmd == "enc":
        print("\n".join(encode_list(a.terms)))
    elif a.cmd == "dec":
        print("\n".join(decode_list(a.terms)))
    else:
        process_json(
            a.file,
            set(a.keys.split(",")),
            "encode" if a.cmd == "enc-json" else "decode",
        )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, InvalidTag) as exc:
        print(f"bamb5 failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
