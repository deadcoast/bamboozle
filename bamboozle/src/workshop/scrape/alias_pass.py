# alias_pass.py
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bamboozle.utils import parse_json, read_bytes
from workshop import config
from workshop.scrape import bamb5

ROOT = config.SCRAPE
TAXONOMY = config.TAXONOMY

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
    def load(cls, path: Path = TAXONOMY) -> Taxonomy:
        raw = parse_json(read_bytes(path))
        if not isinstance(raw, dict):
            raise TypeError("Taxonomy must be an object")
        if type(raw.get("version", 1)) is not int or raw.get("version", 1) != 1:
            raise ValueError("Unsupported taxonomy version")
        ignored = raw.get("ignore", [])
        if not isinstance(ignored, list) or any(
            not isinstance(term, str) for term in ignored
        ):
            raise TypeError("Ignored taxonomy terms must be a list of strings")
        canonical = raw.get("canonical")
        if canonical is None and isinstance(raw.get("categories"), dict):
            canonical = {}
            for category, tags in raw["categories"].items():
                if not isinstance(tags, dict):
                    raise TypeError("Taxonomy categories must contain tag objects")
                for name, aliases in tags.items():
                    if name in canonical:
                        raise ValueError(
                            "Canonical taxonomy term appears in multiple categories"
                        )
                    canonical[name] = {"category": category, "aliases": aliases}
        if not isinstance(canonical, dict):
            raise TypeError("Taxonomy requires canonical terms or categories")
        a2c: dict[str, str] = {}
        cat: dict[str, str] = {}
        for canon, spec in canonical.items():
            if not isinstance(spec, dict) or not isinstance(
                spec.get("aliases", []), list
            ):
                raise TypeError("Invalid canonical taxonomy entry")
            c = normalize(canon)
            if not c or c in cat or (c in a2c and a2c[c] != c):
                raise ValueError(
                    "Canonical taxonomy term collides with another term or alias"
                )
            category = spec.get("category", "uncategorized")
            if not isinstance(category, str) or not category.strip():
                raise ValueError("Taxonomy category must be a nonempty string")
            cat[c] = category
            a2c[c] = c
            for al in spec.get("aliases", []):
                if not isinstance(al, str):
                    raise TypeError("Taxonomy aliases must be strings")
                n = normalize(al)
                if not n:
                    raise ValueError("Taxonomy aliases cannot be empty")
                if n in a2c and a2c[n] != c:
                    raise ValueError(f"alias '{n}' maps to both '{a2c[n]}' and '{c}'")
                a2c[n] = c
        return cls(a2c, cat, {normalize(x) for x in ignored})

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


def run(loras: dict[str, list[str]], tax: Taxonomy | None = None) -> AliasReport:
    """loras: {lora_id: [triggers, possibly b5_ encoded]}"""
    tax = tax or Taxonomy.load()
    rep = AliasReport()
    owners: dict[str, list[str]] = defaultdict(list)
    c = None

    for lid, trigs in loras.items():
        canon_set: set[str] = set()
        for t in trigs:
            try:
                if t.startswith(bamb5.PREFIX) and c is None:
                    c = bamb5._cipher()
                plain = bamb5.decode(t, c)
            except (InvalidTag, ValueError):
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
    import csv
    import os
    import tempfile

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
    for cn, unknown_ids in sorted(rep.unknown.items()):
        rows.append(
            [run_ts, "unknown", enc(cn), ";".join(sorted(unknown_ids)), "info", ""]
        )
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
    import argparse
    import datetime
    import sys

    p = argparse.ArgumentParser(prog="alias_pass")
    p.add_argument("--manifest", type=Path, default=ROOT / "_lora_manifest.json")
    p.add_argument("--ledger", type=Path, default=ROOT / "audit_ledger.csv")
    p.add_argument("--encode-ledger", action="store_true")
    p.add_argument("--fail-on", choices=["none", "decode", "high"], default="decode")
    a = p.parse_args()

    tax = Taxonomy.load()
    rep = run(load_prod_triggers(a.manifest), tax)
    ts = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
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
