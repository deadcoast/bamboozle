import json

import pytest

from workshop.scrape import bamb5
from workshop.scrape import lookup as lk


def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(bamb5, "KEY_PATH", tmp_path / "k")
    bamb5.init_key()
    tx = tmp_path / "taxonomy.json"
    tx.write_text(
        json.dumps(
            {
                "canonical": {
                    "red hair": {"category": "anatomy", "aliases": ["redhead"]},
                    "film grain": {"category": "style", "aliases": []},
                },
                "ignore": ["masterpiece"],
            }
        )
    )
    mf = tmp_path / "m.json"
    mf.write_text(
        json.dumps(
            {
                "loras": {
                    "A": {"triggers": [bamb5.encode("redhead")]},
                    "B": {"triggers": ["(red_hair:1.1)", "neon"]},
                }
            }
        )
    )
    return lk.Index.load(mf, tx, tmp_path / "idx.json")


def test_who_alias_and_token(tmp_path, monkeypatch):
    idx = _env(tmp_path, monkeypatch)
    assert idx.who("Redhead")["loras"] == ["A", "B"]
    assert idx.who(bamb5.encode("red hair"))["canon"] == "red hair"


@pytest.mark.parametrize("trigger", ["", "masterpiece", "b5_zzzz"])
def test_who_ignored_or_undecodable(tmp_path, monkeypatch, trigger):
    idx = _env(tmp_path, monkeypatch)
    assert idx.who(trigger) == {"canon": None, "known": False, "loras": []}


def test_who_unknown_term(tmp_path, monkeypatch):
    idx = _env(tmp_path, monkeypatch)
    assert idx.who("NEON") == {
        "canon": "neon",
        "category": "uncategorized",
        "known": False,
        "loras": ["B"],
    }


def test_stack_flags_and_orphans(tmp_path, monkeypatch):
    idx = _env(tmp_path, monkeypatch)
    r = idx.stack(["A", "B", "Z"])
    assert r["missing"] == ["Z"] and "red hair" in r["collisions"]
    assert idx.orphans() == ["film grain"]


def test_cache_roundtrip(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    raw = json.loads((tmp_path / "idx.json").read_text())
    assert all(k.startswith("b5_") for k in raw["by_canon"])


@pytest.mark.parametrize("damage", ["json", "schema", "ciphertext"])
def test_corrupt_cache_logs_and_rebuilds(tmp_path, monkeypatch, caplog, damage):
    expected = _env(tmp_path, monkeypatch)
    path = tmp_path / "idx.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if damage == "json":
        damaged = "{"
    elif damage == "schema":
        damaged = "[]"
    else:
        raw["by_canon"] = {"b5_aaaaaaaa": ["A"]}
        damaged = json.dumps(raw)
    path.write_text(damaged, encoding="utf-8")

    rebuilt = lk.Index.load(tmp_path / "m.json", tmp_path / "taxonomy.json", path)

    assert rebuilt.by_canon == expected.by_canon
    assert rebuilt.by_lora == expected.by_lora
    assert "Rebuilding lookup index" in caplog.text
    assert json.loads(path.read_text(encoding="utf-8"))["fingerprint"] == expected.fp
