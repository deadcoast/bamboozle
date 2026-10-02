import json

import alias_pass as ap
import bamb5


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
