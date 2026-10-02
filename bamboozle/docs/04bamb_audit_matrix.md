# `audit_matrix.py`

Directory anchor

```txt
D:\AI\ComfyUI_portable\workshop\scrape\
├── audit_matrix.py          # existing, gets a 6-line hook
├── alias_pass.py            # new
├── bamb5.py                 # Part 9
├── taxonomy.json            # canonical → aliases
├── _compatibility_matrix.json
├── audit_ledger.csv
└── tests\test_alias_pass.py
```

1. Decodes every `b5_` trigger through `bamb5.decode_list`. Plaintext passes through unchanged.
2. Normalizes each trigger: NFKC, lowercase, collapse whitespace and underscores, and strip ComfyUI weight syntax like `(term:1.2)`.
3. Resolves aliases to canonical forms using `taxonomy.json`.
4. Detects conflicts on canonical forms, so `red_hair`, `Red Hair`, and `(red hair:1.1)` count as one trigger instead of three.
5. Reports unknown triggers, meaning ones with no taxonomy entry, so you can grow the taxonomy over time.
6. Keeps the decoded and canonical values in memory only. The ledger can store encoded tokens if `--encode-ledger` is set.

Taxonomy schema (`taxonomy.json`)

```json
{
  "version": 1,
  "canonical": {
    "red hair": {
      "category": "anatomy",
      "aliases": ["redhead", "ginger hair", "red_hair"]
    },
    "film grain": {
      "category": "style",
      "aliases": ["grainy", "35mm grain"]
    }
  },
  "ignore": ["masterpiece", "best quality", "highres"]
}
```

`alias_pass.py`

```python
# alias_pass.py
from __future__ import annotations
import json, re, unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import bamb5

ROOT = Path(r"D:\AI\ComfyUI_portable\workshop\scrape")
TAXONOMY = ROOT / "taxonomy.json"

_WEIGHT = re.compile(r"^\((.+?)(?::[\d.]+)?\)$")
_WS = re.compile(r"[\s_]+")


def normalize(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).strip()
    while m := _WEIGHT.match(t):
        t = m.group(1).strip()
    t = t.strip("[]{}").lower()
    return _WS.sub(" ", t).strip(" ,.")


@dataclass
class Taxonomy:
    alias_to_canon: dict[str, str]
    canon_cat: dict[str, str]
    ignore: set[str]

    @classmethod
    def load(cls, path: Path = TAXONOMY) -> "Taxonomy":
        raw = json.loads(path.read_text(encoding="utf-8"))
        a2c, cat = {}, {}
        for canon, spec in raw.get("canonical", {}).items():
            c = normalize(canon)
            cat[c] = spec.get("category", "uncategorized")
            a2c[c] = c
            for al in spec.get("aliases", []):
                n = normalize(al)
                if n in a2c and a2c[n] != c:
                    raise ValueError(f"alias '{n}' maps to both '{a2c[n]}' and '{c}'")
                a2c[n] = c
        return cls(a2c, cat, {normalize(x) for x in raw.get("ignore", [])})

    def resolve(self, term: str) -> str | None:
        n = normalize(term)
        if not n or n in self.ignore:
            return None
        return self.alias_to_canon.get(n, n)

    def known(self, canon: str) -> bool:
        return canon in self.canon_cat


@dataclass
class AliasReport:
    canon_by_lora: dict[str, set[str]] = field(default_factory=dict)
    conflicts: dict[str, list[str]] = field(default_factory=dict)  # canon -> loras
    unknown: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    decode_errors: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
```

You're right, it cut off mid-loop. Here's the full Part 10 from `run()` onward, plus the hook, tests, runbook, and failure modes.

Part 10 (cont.): `alias_pass.py`, completed

```python
# alias_pass.py  (continued from AliasReport)


def run(loras: dict[str, list[str]], tax: Taxonomy | None = None) -> AliasReport:
    """loras: {lora_id: [triggers, possibly b5_ encoded]}"""
    tax = tax or Taxonomy.load()
    rep = AliasReport()
    owners: dict[str, list[str]] = defaultdict(list)
    c = bamb5._cipher()

    for lid, trigs in loras.items():
        canon_set: set[str] = set()
        for t in trigs:
            try:
                plain = bamb5.decode(t, c)
            except Exception:
                rep.decode_errors[lid].append(t)
                continue
            canon = tax.resolve(plain)
            if canon is None:
                continue
            canon_set.add(canon)
            if not tax.known(canon):
                rep.unknown[canon].add(lid)
        rep.canon_by_lora[lid] = canon_set
        for cn in canon_set:
            owners[cn].append(lid)

    rep.conflicts = {cn: sorted(ids) for cn, ids in owners.items() if len(ids) > 1}
    return rep


# ---------- conflict scoring ----------
def conflict_severity(canon: str, loras: list[str], tax: Taxonomy) -> str:
    """Same category + shared canonical trigger = likely real collision."""
    if not tax.known(canon):
        return "low"  # unknown term, can't judge yet
    return "high" if len(loras) >= 3 else "medium"


def pairwise_overlap(rep: AliasReport) -> dict[tuple[str, str], float]:
    """Jaccard overlap of canonical trigger sets per LoRA pair (>0 only)."""
    ids = sorted(rep.canon_by_lora)
    out = {}
    for i, a in enumerate(ids):
        sa = rep.canon_by_lora[a]
        for b in ids[i + 1 :]:
            sb = rep.canon_by_lora[b]
            if not sa or not sb:
                continue
            inter = len(sa & sb)
            if inter:
                out[(a, b)] = round(inter / len(sa | sb), 3)
    return out


# ---------- loaders ----------
def load_prod_triggers(manifest: Path) -> dict[str, list[str]]:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    items = data.get("loras", data) if isinstance(data, dict) else data
    out = {}
    if isinstance(items, dict):
        for lid, spec in items.items():
            out[lid] = spec.get("triggers", []) + spec.get("trained_words", [])
    else:
        for spec in items:
            out[spec["id"]] = spec.get("triggers", []) + spec.get("trained_words", [])
    return out


# ---------- ledger ----------
LEDGER_FIELDS = ["run_ts", "kind", "canon", "loras", "severity", "detail"]


def write_ledger(
    rep: AliasReport,
    tax: Taxonomy,
    path: Path,
    run_ts: str,
    encode_ledger: bool = False,
) -> int:
    import csv, os, tempfile

    c = bamb5._cipher() if encode_ledger else None
    enc = (lambda s: bamb5.encode(s, c)) if encode_ledger else (lambda s: s)

    rows = []
    for cn, ids in sorted(rep.conflicts.items()):
        rows.append(
            [
                run_ts,
                "conflict",
                enc(cn),
                ";".join(ids),
                conflict_severity(cn, ids, tax),
                tax.canon_cat.get(cn, ""),
            ]
        )
    for cn, ids in sorted(rep.unknown.items()):
        rows.append([run_ts, "unknown", enc(cn), ";".join(sorted(ids)), "info", ""])
    for lid, toks in sorted(rep.decode_errors.items()):
        rows.append([run_ts, "decode_error", "", lid, "high", f"{len(toks)} tokens"])

    exists = path.exists()
    prev = path.read_text(encoding="utf-8") if exists else ""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        if prev:
            f.write(prev if prev.endswith("\n") else prev + "\n")
        w = csv.writer(f)
        if not exists:
            w.writerow(LEDGER_FIELDS)
        w.writerows(rows)
    os.replace(tmp, path)
    return len(rows)


# ---------- CLI ----------
def main():
    import argparse, datetime, sys

    p = argparse.ArgumentParser(prog="alias_pass")
    p.add_argument("--manifest", type=Path, default=ROOT / "_lora_manifest.json")
    p.add_argument("--ledger", type=Path, default=ROOT / "audit_ledger.csv")
    p.add_argument("--encode-ledger", action="store_true")
    p.add_argument("--fail-on", choices=["none", "decode", "high"], default="decode")
    a = p.parse_args()

    tax = Taxonomy.load()
    rep = run(load_prod_triggers(a.manifest), tax)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    n = write_ledger(rep, tax, a.ledger, ts, a.encode_ledger)

    highs = sum(
        conflict_severity(cn, ids, tax) == "high" for cn, ids in rep.conflicts.items()
    )
    print(
        f"loras={len(rep.canon_by_lora)} conflicts={len(rep.conflicts)} "
        f"high={highs} unknown={len(rep.unknown)} "
        f"decode_errors={sum(map(len, rep.decode_errors.values()))} rows={n}"
    )

    if a.fail_on == "decode" and rep.decode_errors:
        sys.exit(2)
    if a.fail_on == "high" and (rep.decode_errors or highs):
        sys.exit(3)


if __name__ == "__main__":
    main()
```

Hook into `audit_matrix.py`

Drop this in where the old raw-string conflict check ran:

```python
from alias_pass import run as alias_run, Taxonomy, load_prod_triggers, pairwise_overlap

tax = Taxonomy.load()
alias_rep = alias_run(load_prod_triggers(MANIFEST), tax)
overlap = pairwise_overlap(alias_rep)

for (a, b), j in overlap.items():
    key = f"{a}|{b}"
    entry = matrix.setdefault(key, {})
    entry["trigger_overlap"] = j
    entry["trigger_conflict"] = j >= 0.5  # tune threshold
```

This replaces string-equality conflicts with canonical overlap, so the `audit_ledger.csv` stops filling with false flags.

Tests (`tests/test_alias_pass.py`)

```python
import json, bamb5, alias_pass as ap


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(bamb5, "KEY_PATH", tmp_path / "k")
    bamb5.init_key()
    tx = tmp_path / "taxonomy.json"
    tx.write_text(
        json.dumps(
            {
                "canonical": {
                    "red hair": {
                        "category": "anatomy",
                        "aliases": ["redhead", "red_hair"],
                    }
                },
                "ignore": ["masterpiece"],
            }
        )
    )
    return ap.Taxonomy.load(tx)


def test_normalize():
    assert ap.normalize("(Red_Hair:1.2)") == "red hair"
    assert ap.normalize("  [film  grain] ") == "film grain"


def test_alias_conflict(tmp_path, monkeypatch):
    tax = _setup(tmp_path, monkeypatch)
    rep = ap.run(
        {"A": [bamb5.encode("redhead")], "B": ["(red_hair:1.1)", "masterpiece"]}, tax
    )
    assert rep.conflicts == {"red hair": ["A", "B"]}
    assert not rep.unknown


def test_unknown_and_decode_error(tmp_path, monkeypatch):
    tax = _setup(tmp_path, monkeypatch)
    rep = ap.run({"A": ["weird term", "b5_zzzz"]}, tax)
    assert "weird term" in rep.unknown
    assert rep.decode_errors["A"] == ["b5_zzzz"]
```

Runbook

1. Seed `taxonomy.json` with your 20 most common triggers first, then grow it from the `unknown` rows in the ledger.
2. Run `python alias_pass.py --fail-on decode`.
3. Add this to `promote_trigger.ps1` after `audit_matrix.py`:

   ```powershell
   python alias_pass.py --fail-on decode
   if ($LASTEXITCODE -ne 0) { Write-Error "alias_pass failed ($LASTEXITCODE)"; exit $LASTEXITCODE }
   ```

4. Run `pytest tests\test_alias_pass.py -q` before scraping in the 04:00 job.

Failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| Exit 2, `decode_error` rows | Key rotated or missing for SYSTEM | Restore `_bamb5.key` and run `icacls` for SYSTEM |
| `ValueError: alias maps to both` | Duplicate alias in taxonomy | Remove it from one canonical entry |
| Huge `unknown` count | Taxonomy too thin | Promote frequent unknowns to canonical entries |
| Everything conflicts | Generic triggers like `woman` or `1girl` | Add them to `ignore` |
| Ledger is unreadable | `--encode-ledger` was on | Run `python bamb5.py dec <token>` |
| Missing LoRAs in report | Manifest uses a different key than `triggers` or `trained_words` | Extend `load_prod_triggers` |

---
