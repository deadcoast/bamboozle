# translatePart 9 — `bamb5.py`, the term ciphertranslate

Complete Vigenère encrypt/decrypt in Python that preserves case and passes non-letters through unchanged:

```python
def _shift_char(c, k, decrypt=False):
    if not c.isalpha():
        return c
    base = ord('A') if c.isupper() else ord('a')
    s = -k if decrypt else k
    return chr((ord(c) - base + s) % 26 + base)

def vigenere(text: str, key: str, decrypt: bool = False) -> str:
    key = [ord(k.lower()) - ord('a') for k in key if k.isalpha()]
    if not key:
        raise ValueError("Key must contain at least one letter")
    out, i = [], 0
    for c in text:
        if c.isalpha():
            out.append(_shift_char(c, key[i % len(key)], decrypt))
            i += 1
        else:
            out.append(c)
    return "".join(out)

def encrypt(text, key): return vigenere(text, key)
def decrypt(text, key): return vigenere(text, key, decrypt=True)

if __name__ == "__main__":
    ct = encrypt("Attack at dawn!", "LEMON")
    print(ct)               # Lxfopv ef rnhr!
    print(decrypt(ct, "LEMON"))
```

> translateDirectory anchortranslate

```txt
D:\AI\ComfyUI_portable\workshop\scrape\
├── bamb5.py
├── _bamb5.key          # generated once, keep out of backups you share
└── tests\test_bamb5.py
```

translateDesigntranslate

- translateAES-SIV (deterministic AEAD).translate The same term always gives the same token, so you can still grep, dedupe, and join on encoded fields in `_lora_manifest.json` and `audit_ledger.csv`.
- translateTokens use the `b5_` prefix plus base32 (lowercase, no padding).translate They are safe in filenames, CSV, JSON, and ComfyUI widget fields.
- translateTamper detection.translate A modified token fails to decode instead of returning garbage.
- translateBulk mode.translate It walks JSON manifests and encodes or decodes listed keys in place, atomically.

```python
# bamb5.py
from __future__ import annotations
import base64, json, os, sys, tempfile, argparse
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESSIV

ROOT = Path(r"D:\AI\ComfyUI_portable\workshop\scrape")
KEY_PATH = ROOT / "_bamb5.key"
PREFIX = "b5_"
AD = b"bamb5-v1"

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
    return c.decrypt(_b32d(token[len(PREFIX):]), [AD]).decode("utf-8")

def encode_list(terms, c=None):
    c = c or _cipher(); return [encode(t, c) for t in terms]

def decode_list(tokens, c=None):
    c = c or _cipher(); return [decode(t, c) for t in tokens]

# ---------- bulk JSON ----------
def _walk(obj, keys, fn):
    if isinstance(obj, dict):
        return {k: (_apply(v, fn) if k in keys else _walk(v, keys, fn))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, keys, fn) for v in obj]
    return obj

def _apply(v, fn):
    if isinstance(v, str): return fn(v)
    if isinstance(v, list): return [fn(x) if isinstance(x, str) else x for x in v]
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
    i = sub.add_parser("init"); i.add_argument("--force", action="store_true")
    for name in ("enc", "dec"):
        s = sub.add_parser(name); s.add_argument("terms", nargs="+")
    for name in ("enc-json", "dec-json"):
        s = sub.add_parser(name)
        s.add_argument("file", type=Path)
        s.add_argument("--keys", default="triggers,tags,trained_words")
    a = p.parse_args()

    if a.cmd == "init": init_key(a.force)
    elif a.cmd == "enc": print("\n".join(encode_list(a.terms)))
    elif a.cmd == "dec": print("\n".join(decode_list(a.terms)))
    else:
        process_json(a.file, set(a.keys.split(",")),
                     "encode" if a.cmd == "enc-json" else "decode")

if __name__ == "__main__":
    main()
```

translateTeststranslate

```python
# tests/test_bamb5.py
import bamb5, pytest
from cryptography.exceptions import InvalidTag

def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(bamb5, "KEY_PATH", tmp_path / "k")
    bamb5.init_key()
    t = bamb5.encode("some trigger")
    assert t.startswith("b5_") and bamb5.decode(t) == "some trigger"
    assert bamb5.encode("some trigger") == t       # deterministic
    assert bamb5.encode(t) == t                    # idempotent

def test_tamper(tmp_path, monkeypatch):
    monkeypatch.setattr(bamb5, "KEY_PATH", tmp_path / "k")
    bamb5.init_key()
    t = bamb5.encode("x")
    bad = t[:-2] + ("a" if t[-2] != "a" else "b") + t[-1]
    with pytest.raises(Exception):
        bamb5.decode(bad)
```

translateRunbooktranslate

1. Run `pip install cryptography pytest`.
2. Run `python bamb5.py init` once, then back up `_bamb5.key` somewhere private.
3. For single terms, use `python bamb5.py enc "term one" "term two"` and `python bamb5.py dec b5_...`.
4. For a manifest, use `python bamb5.py enc-json _lora_manifest.json --keys triggers,tags`.
5. For the hook in `promote.py`, call `bamb5.encode_list(sidecar["triggers"])` before writing the sidecar, and `decode_list` in `audit_matrix.py` before conflict checks.
6. In `promote_trigger.ps1`, add `pytest tests\test_bamb5.py -q` before the scrape step and exit if it fails.

translateFailure modestranslate

| Symptom | Cause | Fix |
| --- | --- | --- |
| `No key` | `_bamb5.key` missing | Run `init`, or restore it from backup |
| `InvalidTag` on decode | Wrong key, or the token was edited | Restore the correct key, then re-encode from source |
| Lost key | No backup exists | Encoded data is unrecoverable, so back up the key |
| Double-encoding | Bulk run repeated | Safe, because the `b5_` prefix makes encode a no-op |
| SYSTEM task can't read the key | ACL on the key file | Grant SYSTEM read access with `icacls _bamb5.key /grant SYSTEM:R` |

Want Part 10 next (the `audit_matrix.py` alias pass, reading decoded triggers), or should I wire this into `promote.py` directly?
