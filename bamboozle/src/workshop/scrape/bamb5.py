"""workshop_mcp — MCP tools over the LoRA promote/audit pipeline."""

# bamb5.py
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from fastmcp import FastMCP

ROOT = Path(r"D:\AI\ComfyUI_portable\workshop\scrape")
KEY_PATH = ROOT / "_bamb5.key"
PREFIX = "b5_"
AD = b"bamb5-v1"
WORKSHOP = ROOT / "workshop"
SCRAPE = WORKSHOP / "scrape"
PY = ROOT / "python_embeded" / "python.exe"
MANIFEST = SCRAPE / "_lora_manifest.json"
MATRIX = SCRAPE / "_compatibility_matrix.json"
LEDGER = SCRAPE / "_lora_ledger.csv"
TAXONOMY = Path(__file__).parent / "taxonomy.json"

mcp = FastMCP("workshop")


# ---------- key ----------
def init_key(force: bool = False) -> None:
    if KEY_PATH.exists() and not force:
        raise SystemExit(f"Key exists: {KEY_PATH} (use --force to overwrite)")
    KEY_PATH.write_bytes(AESSIV.generate_key(512))
    print(f"Wrote {KEY_PATH}")


def _cipher() -> AESSIV:
    if not KEY_PATH.exists():
        raise SystemExit("No key. Run: python bamb5.py init")
    return AESSIV(KEY_PATH.read_bytes())


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


# ---------- core ----------
def _b32e(b: bytes) -> str:
    return base64.b32encode(b).decode().rstrip("=").lower()


def _b32d(s: str) -> bytes:
    s = s.upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def encode(term: str, c: AESSIV | None = None) -> str:
    if term.startswith(PREFIX):
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
    c = _cipher()
    fn = (lambda s: encode(s, c)) if mode == "encode" else (lambda s: decode(s, c))
    data = json.loads(path.read_text(encoding="utf-8"))
    out = _walk(data, keys, fn)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
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
    main()
