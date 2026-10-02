# Reverse-lookup query surface

Directory anchor

```txt
D:\AI\ComfyUI_portable\workshop\scrape\
├── alias_pass.py            # Part 10, source of canon_by_lora
├── bamb5.py                 # Part 9
├── lookup.py                # new: index + query API + CLI
├── _lookup_index.json       # cached index (canon keys b5_-encoded at rest)
├── taxonomy.json
├── _lora_manifest.json
├── workshop_mcp.py          # gets 6 tool registrations
└── tests\test_lookup.py
```

What it does

1. Builds a reverse index from `rep.canon_by_lora`: canon → LoRAs, LoRA → canon, and category → canon.
2. Resolves queries through the same pipeline as Part 10. A query can be a `b5_` token, an alias, or weighted syntax like `(redhead:1.2)`. All three resolve to the same canonical entry.
3. Caches the index to `_lookup_index.json`, keyed by a fingerprint of the manifest and taxonomy. It rebuilds automatically when either file changes.
4. Encodes canon keys with `bamb5` before writing to disk. AES-SIV is deterministic, so the cached index can still be searched, while decoded values stay in memory only.
5. Exposes six queries:
   - `who`: which LoRAs use a trigger
   - `of`: which triggers a LoRA has
   - `cat`: everything in a category
   - `suggest`: fuzzy matches for a term
   - `stack`: pre-flight collision check for a LoRA stack
   - `orphans`: taxonomy entries that no LoRA uses

Index schema (`_lookup_index.json`)

```json
{
  "version": 1,
  "fingerprint": "3f9a1c0be2d47a18",
  "built": "2026-10-02T14:00:00",
  "by_canon": { "b5_k3j...": ["lora_a", "lora_b"] },
  "by_lora":  { "lora_a": ["b5_k3j...", "b5_q7m..."] }
}
```

Category maps are rebuilt from `taxonomy.json` on load, so they are never stored on disk.

> `lookup.py`

```python
# lookup.py
from __future__ import annotations
import json, os, tempfile, hashlib, difflib, datetime
from collections import defaultdict
from pathlib import Path
import bamb5
import alias_pass as ap

ROOT = ap.ROOT
MANIFEST = ROOT / "_lora_manifest.json"
INDEX = ROOT / "_lookup_index.json"
VERSION = 1


def _fingerprint(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        st = p.stat()
        h.update(f"{p.name}:{st.st_mtime_ns}:{st.st_size}".encode())
    return h.hexdigest()[:16]


def _atomic_write(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


class Index:
    def __init__(self, by_canon: dict[str, list[str]], by_lora: dict[str, list[str]],
                 tax: ap.Taxonomy, fp: str):
        self.by_canon = by_canon
        self.by_lora = by_lora
        self.tax = tax
        self.fp = fp
        self.by_cat: dict[str, list[str]] = defaultdict(list)
        for cn in sorted(set(by_canon) | set(tax.canon_cat)):
            self.by_cat[tax.canon_cat.get(cn, "uncategorized")].append(cn)

    # ---------- build / persist ----------
    @classmethod
    def build(cls, manifest: Path = MANIFEST, tax_path: Path = ap.TAXONOMY) -> "Index":
        tax = ap.Taxonomy.load(tax_path)
        rep = ap.run(ap.load_prod_triggers(manifest), tax)
        by_lora = {lid: sorted(cs) for lid, cs in rep.canon_by_lora.items()}
        by_canon: dict[str, list[str]] = defaultdict(list)
        for lid, cs in by_lora.items():
            for cn in cs:
                by_canon[cn].append(lid)
        return cls({k: sorted(v) for k, v in by_canon.items()}, by_lora, tax,
                   _fingerprint(manifest, tax_path))

    def save(self, path: Path = INDEX) -> None:
        c = bamb5._cipher()
        e = lambda s: bamb5.encode(s, c)
        _atomic_write(path, {
            "version": VERSION,
            "fingerprint": self.fp,
            "built": datetime.datetime.now().isoformat(timespec="seconds"),
            "by_canon": {e(k): v for k, v in self.by_canon.items()},
            "by_lora": {k: [e(x) for x in v] for k, v in self.by_lora.items()},
        })

    @classmethod
    def load(cls, manifest: Path = MANIFEST, tax_path: Path = ap.TAXONOMY,
             path: Path = INDEX, force: bool = False) -> "Index":
        fp = _fingerprint(manifest, tax_path)
        if not force and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("version") == VERSION and raw.get("fingerprint") == fp:
                    c = bamb5._cipher()
                    d = lambda s: bamb5.decode(s, c)
                    return cls({d(k): v for k, v in raw["by_canon"].items()},
                               {k: [d(x) for x in v] for k, v in raw["by_lora"].items()},
                               ap.Taxonomy.load(tax_path), fp)
            except Exception:
                pass   # stale key, corrupt file, schema drift -> rebuild
        idx = cls.build(manifest, tax_path)
        idx.save(path)
        return idx

    # ---------- queries ----------
    def _canon(self, q: str) -> str | None:
        return self.tax.resolve(bamb5.decode(q))

    def who(self, trigger: str) -> dict:
        cn = self._canon(trigger)
        if cn is None:
            return
```

```python
# lookup.py
from __future__ import annotations
import json, os, tempfile, hashlib, difflib, datetime
from collections import defaultdict
from pathlib import Path
import bamb5
import alias_pass as ap

ROOT = ap.ROOT
MANIFEST = ROOT / "_lora_manifest.json"
INDEX = ROOT / "_lookup_index.json"
VERSION = 1


def _fingerprint(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        st = p.stat()
        h.update(f"{p.name}:{st.st_mtime_ns}:{st.st_size}".encode())
    return h.hexdigest()[:16]


def _atomic_write(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


class Index:
    def __init__(self, by_canon: dict[str, list[str]], by_lora: dict[str, list[str]],
                 tax: ap.Taxonomy, fp: str):
        self.by_canon = by_canon
        self.by_lora = by_lora
        self.tax = tax
        self.fp = fp
        self.by_cat: dict[str, list[str]] = defaultdict(list)
        for cn in sorted(set(by_canon) | set(tax.canon_cat)):
            self.by_cat[tax.canon_cat.get(cn, "uncategorized")].append(cn)

    # ---------- build / persist ----------
    @classmethod
    def build(cls, manifest: Path = MANIFEST, tax_path: Path = ap.TAXONOMY) -> "Index":
        tax = ap.Taxonomy.load(tax_path)
        rep = ap.run(ap.load_prod_triggers(manifest), tax)
        by_lora = {lid: sorted(cs) for lid, cs in rep.canon_by_lora.items()}
        by_canon: dict[str, list[str]] = defaultdict(list)
        for lid, cs in by_lora.items():
            for cn in cs:
                by_canon[cn].append(lid)
        return cls({k: sorted(v) for k, v in by_canon.items()}, by_lora, tax,
                   _fingerprint(manifest, tax_path))

    def save(self, path: Path = INDEX) -> None:
        c = bamb5._cipher()
        e = lambda s: bamb5.encode(s, c)
        _atomic_write(path, {
            "version": VERSION,
            "fingerprint": self.fp,
            "built": datetime.datetime.now().isoformat(timespec="seconds"),
            "by_canon": {e(k): v for k, v in self.by_canon.items()},
            "by_lora": {k: [e(x) for x in v] for k, v in self.by_lora.items()},
        })

    @classmethod
    def load(cls, manifest: Path = MANIFEST, tax_path: Path = ap.TAXONOMY,
             path: Path = INDEX, force: bool = False) -> "Index":
        fp = _fingerprint(manifest, tax_path)
        if not force and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("version") == VERSION and raw.get("fingerprint") == fp:
                    c = bamb5._cipher()
                    d = lambda s: bamb5.decode(s, c)
                    return cls({d(k): v for k, v in raw["by_canon"].items()},
                               {k: [d(x) for x in v] for k, v in raw["by_lora"].items()},
                               ap.Taxonomy.load(tax_path), fp)
            except Exception:
                pass   # stale key, corrupt file, schema drift -> rebuild
        idx = cls.build(manifest, tax_path)
        idx.save(path)
        return idx

    # ---------- queries ----------
    def _canon(self, q: str) -> str | None:
        return self.tax.resolve(bamb5.decode(q))

    def who(self, trigger: str) -> dict:
        cn = self._canon(trigger)
        if cn is None:
            return  []}
        return on_cat.get(cn, "uncategorized"),
                "known": self.tax.known(cn),
                "loras": self.by_canon.get(cn, []),
                "suggest": [] if cn in self.by_canon else self.suggest(cn, 3)}

    def of(self, lora_id: str) -> dict:
        lid = lora_id
        if lid not in self.by_lora:
            m = difflib.get_close_matches(lora_id, self.by_lora.keys(), n=1, cutoff=0.6)
            if not m:
                return {"lora": lora_id, "found": False, "triggers": []}
            lid = m[0]
        return {"lora": lid, "found": True, "matched_from": lora_id if lid != lora_id else None,
                "triggers": [{"canon": cn,
                              "category": self.tax.canon_cat.get(cn, "uncategorized"),
                              "shared_with": [x for x in self.by_canon[cn] if x != lid]}
                             for cn in self.by_lora[lid]]}

    def cat(self, category: str) -> dict:
        cat = category.lower().strip()
        return {"category": cat,
                "terms": {cn: self.by_canon.get(cn, []) for cn in self.by_cat.get(cat, [])}}

    def suggest(self, term: str, n: int = 5) -> list[str]:
        pool = set(self.tax.alias_to_canon) | set(self.by_canon)
        hits = difflib.get_close_matches(ap.normalize(bamb5.decode(term)), pool, n=n * 2, cutoff=0.6)
        out: list[str] = []
        for h in hits:
            cn = self.tax.alias_to_canon.get(h, h)
            if cn not in out:
                out.append(cn)
        return out[:n]

    def stack(self, lora_ids: list[str], threshold: float = 0.5) -> dict:
        missing = [x for x in lora_ids if x not in self.by_lora]
        ids = [x for x in lora_ids if x in self.by_lora]
        shared: dict[str, list[str]] = defaultdict(list)
        for lid in ids:
            for cn in self.by_lora[lid]:
                shared[cn].append(lid)
        shared = {cn: v for cn, v in shared.items() if len(v) > 1}
        pairs = []
        for i, a in enumerate(ids):
            sa = set(self.by_lora[a])
            for b in ids[i + 1:]:
                sb = set(self.by_lora[b])
                if sa and sb and (inter := sa & sb):
                    j = round(len(inter) / len(sa | sb), 3)
                    pairs.append({"a": a, "b": b, "jaccard": j, "conflict": j >= threshold})
        return {"stack": ids, "missing": missing, "shared_triggers": shared,
                "pairs": sorted(pairs, key=lambda p: -p["jaccard"]),
                "verdict": "conflict" if any(p["conflict"] for p in pairs)
                           else ("overlap" if shared else "clean")}

    def orphans(self) -> list[str]:
        return sorted(cn for cn in self.tax.canon_cat if cn not in self.by_canon)


# ---------- CLI ----------
def main():
    import argparse, sys
    p = argparse.ArgumentParser(prog="lookup")
    p.add_argument("--json", action="store_true")
    p.add_argument("--encode-out", action="store_true", help="b5_-encode canon terms in output")
    p.add_argument("--rebuild", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("who").add_argument("trigger")
    sub.add_parser("of").add_argument("lora")
    sub.add_parser("cat").add_argument("category")
    s = sub.add_parser("suggest"); s.add_argument("term"); s.add_argument("-n", type=int, default=5)
    st = sub.add_parser("stack"); st.add_argument("loras", nargs="+")
    st.add_argument("--threshold", type=float, default=0.5)
    sub.add_parser("orphans")
    sub.add_parser("rebuild")
    a = p.parse_args()

    idx = Index.load(force=a.rebuild or a.cmd == "rebuild")
    res = {
        "who": lambda: idx.who(a.trigger),
        "of": lambda: idx.of(a.lora),
        "cat": lambda: idx.cat(a.category),
        "suggest": lambda: idx.suggest(a.term, a.n),
        "stack": lambda: idx.stack(a.loras, a.threshold),
        "orphans": lambda: idx.orphans(),
        "rebuild": lambda: {"loras": len(idx.by_lora), "canon": len(idx.by_canon), "fp": idx.fp},
    }[a.cmd]()

    if a.encode_out:
        c = bamb5._cipher()
        res = json.loads(json.dumps(res), object_hook=lambda d: {
            k: (bamb5.encode(v, c) if k == "canon" and isinstance(v, str) else v) for k, v in d.items()})

    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json or not isinstance(res, list)
          else "\n".join(res))
    if a.cmd == "stack" and res["verdict"] == "conflict":
        sys.exit(4)

if __name__ == "__main__":
    main()
```

> `workshop_mcp.py` registration

This assumes the FastMCP-style server from the `mcp` SDK. If your file uses a different decorator, swap it in.

```python
from lookup import Index

_idx: Index | None = None
def _get() -> Index:
    global _idx
    _idx = Index.load()          # cheap: fingerprint check, rebuild only on change
    return _idx

@mcp.tool()
def lora_who(trigger: str) -> dict:
    """Which prod LoRAs use this trigger (alias/b5_/weighted ok)."""
    return _get().who(trigger)

@mcp.tool()
def lora_triggers(lora_id: str) -> dict:
    """Canonical triggers of a LoRA plus which LoRAs share each."""
    return _get().of(lora_id)

@mcp.tool()
def lora_category(category: str) -> dict:
    """All canonical terms in a category -> LoRAs."""
    return _get().cat(category)

@mcp.tool()
def lora_suggest(term: str, n: int = 5) -> list[str]:
    """Fuzzy canonical matches for a term."""
    return _get().suggest(term, n)

@mcp.tool()
def lora_stack_check(lora_ids: list[str], threshold: float = 0.5) -> dict:
    """Pre-flight trigger collision check for a LoRA stack."""
    return _get().stack(lora_ids, threshold)

@mcp.tool()
def lora_orphans() -> list[str]:
    """Taxonomy terms no prod LoRA uses."""
    return _get().orphans()
```

Hook into Part 4 (LoRA rotation)

Run this check before a rotation stack is queued, so colliding stacks get rerolled instead of rendered:

```python
from lookup import Index
chk = Index.load().stack([l["id"] for l in stack])
if chk["verdict"] == "conflict":
    log.warning("reroll: %s", chk["pairs"][0]); continue
```

Tests (`tests/test_lookup.py`)

```python
import json, bamb5, lookup as lk

def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(bamb5, "KEY_PATH", tmp_path / "k"); bamb5.init_key()
    tx = tmp_path / "taxonomy.json"
    tx.write_text(json.dumps({"canonical": {
        "red hair": {"category": "anatomy", "aliases": ["redhead"]},
        "film grain": {"category": "style", "aliases": []}}}))
    mf = tmp_path / "m.json"
    mf.write_text(json.dumps({"loras": {
        "A": {"triggers": [bamb5.encode("redhead")]},
        "B": {"triggers": ["(red_hair:1.1)", "neon"]}}}))
    return lk.Index.load(mf, tx, tmp_path / "idx.json")

def test_who_alias_and_token(tmp_path, monkeypatch):
    idx = _env(tmp_path, monkeypatch)
    assert idx.who("Redhead")["loras"] == ["A", "B"]
    assert idx.who(bamb5.encode("red hair"))["canon"] == "red hair"

def test_stack_flags_and_orphans(tmp_path, monkeypatch):
    idx = _env(tmp_path, monkeypatch)
    r = idx.stack(["A", "B", "Z"])
    assert r["missing"] == ["Z"] and "red hair" in r["collisions"]
    assert idx.orphans() == ["film grain"]

def test_cache_roundtrip(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    raw = json.loads((tmp_path / "idx.json").read_text())
    assert all(k.startswith("b5_") for k in raw["by_canon"])
```

---
Part 11 cut off inside `who()`, so the first section finishes it. Part 12 then wires it into the MCP server and the scheduled pipeline.

---

**Part 11 (completion): `lookup.py` queries + CLI**

Replace everything from `# ---------- queries ----------` down with this:

```python
    # ---------- queries ----------
    def _canon(self, q: str) -> str | None:
        try:
            plain = bamb5.decode(q)
        except Exception:
            return None
        return self.tax.resolve(plain)

    def who(self, trigger: str) -> dict:
        cn = self._canon(trigger)
        if cn is None:
            return ": False}
        return on_cat.get(cn, "uncategorized"),
                "known": self.tax.known(cn),
                "loras": self.by_canon.get(cn, [])}

    def of(self, lora_id: str) -> dict:
        cs = self.by_lora.get(lora_id)
        if cs is None:
            near = difflib.get_close_matches(lora_id, list(self.by_lora), n=5, cutoff=0.5)
            return {"lora": lora_id, "found": False, "did_you_mean": near}
        grouped: dict[str, list[str]] = defaultdict(list)
        for cn in cs:
            grouped[self.tax.canon_cat.get(cn, "uncategorized")].append(cn)
        return {"lora": lora_id, "found": True, "triggers": dict(grouped)}

    def cat(self, category: str) -> dict:
        out = {cn: self.by_canon.get(cn, []) for cn in self.by_cat.get(category, [])}
        return {"category": category, "terms": out,
                "lora_count": len({l for ls in out.values() for l in ls})}

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
            out.append({"match": h, "canon": cn,
                        "loras": len(self.by_canon.get(cn, []))})
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
            for b in ids[i + 1:]:
                sb = set(self.by_lora[b])
                if sa and sb and (inter := sa & sb):
                    j = round(len(inter) / len(sa | sb), 3)
                    pairs.append({"a": a, "b": b, "jaccard": j,
                                  "flag": j >= threshold, "shared": sorted(inter)})
        return {"stack": lora_ids, "missing": missing,
                "collisions": collisions,
                "pairs": sorted(pairs, key=lambda p: -p["jaccard"]),
                "ok": not missing and not any(p["flag"] for p in pairs)}

    def orphans(self) -> list[str]:
        return sorted(cn for cn in self.tax.canon_cat if cn not in self.by_canon)

    def stats(self) -> dict:
        return {"fingerprint": self.fp, "loras": len(self.by_lora),
                "canon_terms": len(self.by_canon),
                "categories": {k: len(v) for k, v in self.by_cat.items()},
                "orphans": len(self.orphans())}


# ---------- CLI ----------
def main():
    import argparse
    p = argparse.ArgumentParser(prog="lookup")
    p.add_argument("--rebuild", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("who").add_argument("trigger")
    sub.add_parser("of").add_argument("lora")
    sub.add_parser("cat").add_argument("category")
    s = sub.add_parser("suggest"); s.add_argument("term"); s.add_argument("-n", type=int, default=8)
    st = sub.add_parser("stack"); st.add_argument("loras", nargs="+")
    st.add_argument("--threshold", type=float, default=0.5)
    sub.add_parser("orphans"); sub.add_parser("stats")
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
```

---

**Part 12: MCP surface + pipeline gate**

**Directory anchor**
```
D:\AI\ComfyUI_portable\workshop\
├── mcp\workshop_mcp.py          # +7 tools, replaces old query_manifest / conflict tools
└── scrape\
    ├── promote_trigger.ps1      # gains test gate + index rebuild stage
    ├── lookup.py
    ├── alias_pass.py
    └── _pipeline_status.json    # new: last-run summary for the MCP status tool
```

**What it does**
1. Exposes `lookup.Index` to Claude Code and other