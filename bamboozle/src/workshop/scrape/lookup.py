# lookup.py
from __future__ import annotations

import datetime
import difflib
import hashlib
import json
import logging
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from cryptography.exceptions import InvalidTag

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bamboozle.utils import read_bytes
from workshop import config
from workshop.scrape import alias_pass as ap
from workshop.scrape import bamb5

ROOT = ap.ROOT
MANIFEST = ROOT / "_lora_manifest.json"
INDEX = config.INDEX
VERSION = 1
logger = logging.getLogger(__name__)


def _fingerprint(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.name.encode("utf-8"))
        h.update(hashlib.sha256(read_bytes(p)).digest())
    return h.hexdigest()[:16]


def _atomic_write(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


class Index:
    def __init__(
        self,
        by_canon: dict[str, list[str]],
        by_lora: dict[str, list[str]],
        tax: ap.Taxonomy,
        fp: str,
    ):
        self.by_canon = by_canon
        self.by_lora = by_lora
        self.tax = tax
        self.fp = fp
        self.by_cat: dict[str, list[str]] = defaultdict(list)
        for cn in sorted(set(by_canon) | set(tax.canon_cat)):
            self.by_cat[tax.canon_cat.get(cn, "uncategorized")].append(cn)

    # ---------- build / persist ----------
    @classmethod
    def build(cls, manifest: Path = MANIFEST, tax_path: Path = ap.TAXONOMY) -> Index:
        tax = ap.Taxonomy.load(tax_path)
        rep = ap.run(ap.load_prod_triggers(manifest), tax)
        if rep.decode_errors:
            raise ValueError(
                "Manifest contains undecodable triggers; restore the correct key before rebuilding"
            )
        by_lora = {lid: sorted(cs) for lid, cs in rep.canon_by_lora.items()}
        by_canon: dict[str, list[str]] = defaultdict(list)
        for lid, cs in by_lora.items():
            for cn in cs:
                by_canon[cn].append(lid)
        return cls(
            {k: sorted(v) for k, v in by_canon.items()},
            by_lora,
            tax,
            _fingerprint(manifest, tax_path, bamb5.KEY_PATH),
        )

    def save(self, path: Path = INDEX) -> None:
        c = bamb5._cipher()
        e = lambda s: bamb5.encode(s, c)
        _atomic_write(
            path,
            {
                "version": VERSION,
                "fingerprint": self.fp,
                "built": datetime.datetime.now(datetime.UTC).isoformat(
                    timespec="seconds"
                ),
                "by_canon": {e(k): v for k, v in self.by_canon.items()},
                "by_lora": {k: [e(x) for x in v] for k, v in self.by_lora.items()},
            },
        )

    @classmethod
    def load(
        cls,
        manifest: Path = MANIFEST,
        tax_path: Path = ap.TAXONOMY,
        path: Path = INDEX,
        force: bool = False,
    ) -> Index:
        fp = _fingerprint(manifest, tax_path, bamb5.KEY_PATH)
        if not force and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("version") == VERSION and raw.get("fingerprint") == fp:
                    c = bamb5._cipher()
                    d = lambda s: bamb5.decode(s, c)
                    return cls(
                        {d(k): v for k, v in raw["by_canon"].items()},
                        {k: [d(x) for x in v] for k, v in raw["by_lora"].items()},
                        ap.Taxonomy.load(tax_path),
                        fp,
                    )
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                InvalidTag,
            ) as exc:
                logger.warning(
                    "Rebuilding lookup index %s after cache read failed: %s", path, exc
                )
        idx = cls.build(manifest, tax_path)
        idx.save(path)
        return idx

        # ---------- queries ----------

    def _canon(self, q: str) -> str | None:
        try:
            plain = bamb5.decode(q)
        except (InvalidTag, ValueError):
            return None
        return self.tax.resolve(plain)

    def who(self, trigger: str) -> dict:
        cn = self._canon(trigger)
        if cn is None:
            return {"canon": None, "known": False, "loras": []}
        return {
            "canon": cn,
            "category": self.tax.canon_cat.get(cn, "uncategorized"),
            "known": self.tax.known(cn),
            "loras": self.by_canon.get(cn, []),
        }

    def of(self, lora_id: str) -> dict:
        cs = self.by_lora.get(lora_id)
        if cs is None:
            near = difflib.get_close_matches(
                lora_id, list(self.by_lora), n=5, cutoff=0.5
            )
            return {"lora": lora_id, "found": False, "did_you_mean": near}
        grouped: dict[str, list[str]] = defaultdict(list)
        for cn in cs:
            grouped[self.tax.canon_cat.get(cn, "uncategorized")].append(cn)
        return {"lora": lora_id, "found": True, "triggers": dict(grouped)}

    def cat(self, category: str) -> dict:
        out = {cn: self.by_canon.get(cn, []) for cn in self.by_cat.get(category, [])}
        return {
            "category": category,
            "terms": out,
            "lora_count": len({l for ls in out.values() for l in ls}),
        }

    def suggest(self, term: str, n: int = 8) -> list[dict]:
        q = ap.normalize(bamb5.decode(term)) if term else ""
        pool = list(self.tax.alias_to_canon) + list(self.by_canon)
        hits = difflib.get_close_matches(q, list(dict.fromkeys(pool)), n=n, cutoff=0.6)
        seen, out = set(), []
        for h in hits:
            cn = self.tax.alias_to_canon.get(h, h)
            if cn in seen:
                continue
            seen.add(cn)
            out.append(
                {"match": h, "canon": cn, "loras": len(self.by_canon.get(cn, []))}
            )
        return out

    def stack(self, lora_ids: list[str], threshold: float = 0.5) -> dict:
        missing = [l for l in lora_ids if l not in self.by_lora]
        ids = [l for l in lora_ids if l in self.by_lora]
        shared: dict[str, list[str]] = defaultdict(list)
        for l in ids:
            for cn in self.by_lora[l]:
                shared[cn].append(l)
        collisions = {cn: ls for cn, ls in shared.items() if len(ls) > 1}
        pairs = []
        for i, a in enumerate(ids):
            sa = set(self.by_lora[a])
            for b in ids[i + 1 :]:
                sb = set(self.by_lora[b])
                if sa and sb and (inter := sa & sb):
                    j = round(len(inter) / len(sa | sb), 3)
                    pairs.append(
                        {
                            "a": a,
                            "b": b,
                            "jaccard": j,
                            "flag": j >= threshold,
                            "shared": sorted(inter),
                        }
                    )
        return {
            "stack": lora_ids,
            "missing": missing,
            "collisions": collisions,
            "pairs": sorted(pairs, key=lambda p: -p["jaccard"]),
            "ok": not missing and not any(p["flag"] for p in pairs),
        }

    def orphans(self) -> list[str]:
        return sorted(cn for cn in self.tax.canon_cat if cn not in self.by_canon)

    def stats(self) -> dict:
        return {
            "fingerprint": self.fp,
            "loras": len(self.by_lora),
            "canon_terms": len(self.by_canon),
            "categories": {k: len(v) for k, v in self.by_cat.items()},
            "orphans": len(self.orphans()),
        }


# ---------- CLI ----------
def main():
    import argparse

    p = argparse.ArgumentParser(prog="lookup")
    p.add_argument("--rebuild", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("who").add_argument("trigger")
    sub.add_parser("of").add_argument("lora")
    sub.add_parser("cat").add_argument("category")
    s = sub.add_parser("suggest")
    s.add_argument("term")
    s.add_argument("-n", type=int, default=8)
    st = sub.add_parser("stack")
    st.add_argument("loras", nargs="+")
    st.add_argument("--threshold", type=float, default=0.5)
    sub.add_parser("orphans")
    sub.add_parser("stats")
    a = p.parse_args()

    idx = Index.load(force=a.rebuild)
    res = {
        "who": lambda: idx.who(a.trigger),
        "of": lambda: idx.of(a.lora),
        "cat": lambda: idx.cat(a.category),
        "suggest": lambda: idx.suggest(a.term, a.n),
        "stack": lambda: idx.stack(a.loras, a.threshold),
        "orphans": idx.orphans,
        "stats": idx.stats,
    }[a.cmd]()
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if a.cmd == "stack" and not res["ok"]:
        raise SystemExit(4)


if __name__ == "__main__":
    main()
