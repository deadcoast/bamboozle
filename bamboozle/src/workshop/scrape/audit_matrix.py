"""Recompute canonical overlap without inventing model compatibility scores."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV

from bamboozle.utils import json_bytes, parse_json, sha256
from workshop import config
from workshop import operations as ops
from workshop.pipeline import _manifest, _matrix
from workshop.scrape import alias_pass, bamb5


def audit(
    root: Path, *, prune: bool = False, apply: bool = False, threshold: float = 0.5
) -> dict[str, Any]:
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Overlap threshold must be between zero and one")
    ws = ops.workspace(root)
    observed = {name: ws.read(name) for name in ops.CONTROLS}
    manifest, matrix = (
        parse_json(observed[ops.MANIFEST]),
        parse_json(observed[ops.MATRIX]),
    )
    ids = _manifest(manifest)
    _matrix(matrix, None)
    if len(ids) > 1000 or len(matrix["pairs"]) > 50000:
        raise ValueError("Native audit exceeds its LoRA or pair limit")
    taxonomy = ws.resolve("scrape/taxonomy.json")
    tax_bytes = ws.read(taxonomy)
    tax = alias_pass.Taxonomy.load(taxonomy)
    if ws.read(taxonomy) != tax_bytes:
        raise ValueError("Taxonomy changed during audit")
    inputs = {"scrape/taxonomy.json": sha256(tax_bytes)}
    canonical: dict[str, set[str]] = {}
    cipher = None
    for name, entry in manifest["loras"].items():
        terms: set[str] = set()
        for term in (
            entry.get("triggers", [])
            + entry.get("trained_words", [])
            + entry.get("tags", [])
        ):
            if term.startswith(bamb5.PREFIX) and cipher is None:
                key = ws.read("scrape/_bamb5.key")
                cipher = AESSIV(key)
                inputs["scrape/_bamb5.key"] = sha256(key)
            try:
                normalized = tax.resolve(bamb5.decode(term, cipher))
            except InvalidTag as exc:
                raise ValueError(
                    "Undecodable trigger; restore the matching workshop key"
                ) from exc
            if normalized is not None:
                terms.add(normalized)
        canonical[name] = terms
    dangling = sorted(
        pair for pair in matrix["pairs"] if not set(pair.split("|")) <= ids
    )
    if prune:
        for pair in dangling:
            del matrix["pairs"][pair]
    overlaps = alias_pass.pairwise_overlap(
        alias_pass.AliasReport(canon_by_lora=canonical)
    )
    for pair in overlaps:
        matrix["pairs"].setdefault("|".join(pair), {"status": "untested"})
    for pair, entry in matrix["pairs"].items():
        names = pair.split("|")
        if set(names) <= ids:
            overlap = overlaps.get((names[0], names[1]), 0.0)
            entry.update(
                trigger_overlap=overlap,
                trigger_conflict=overlap > 0 and overlap >= threshold,
            )
    plan = ops._plan(
        ws, observed, "audit", "", {ops.MATRIX: json_bytes(matrix)}, inputs=inputs
    )
    if apply and dangling and not prune:
        raise ValueError(
            "Dangling matrix pairs require explicit --prune before applying"
        )
    report = ops._execute(ws, plan, apply)
    report.update(
        dangling_pairs=dangling,
        pruned=prune,
        pairs=len(matrix["pairs"]),
        trigger_conflicts=sum(
            bool(entry.get("trigger_conflict")) for entry in matrix["pairs"].values()
        ),
        compatibility_evaluated=False,
    )
    return report


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=root or config.WORKSHOP)
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                audit(
                    args.root,
                    prune=args.prune,
                    apply=args.apply,
                    threshold=args.threshold,
                ),
                indent=2,
            )
        )
        return 0
    except ops.PendingOperation as exc:
        print(
            json.dumps(
                {
                    "status": "recovery_required",
                    "operation_id": exc.operation_id,
                    "error": str(exc),
                }
            )
        )
        return 1
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
