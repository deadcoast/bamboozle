# tests/test_bamb5.py
import bamb5
import pytest


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(bamb5, "KEY_PATH", tmp_path / "k")
    bamb5.init_key()
    t = bamb5.encode("some trigger")
    assert t.startswith("b5_") and bamb5.decode(t) == "some trigger"
    assert bamb5.encode("some trigger") == t  # deterministic
    assert bamb5.encode(t) == t  # idempotent


def test_tamper(tmp_path, monkeypatch):
    monkeypatch.setattr(bamb5, "KEY_PATH", tmp_path / "k")
    bamb5.init_key()
    t = bamb5.encode("x")
    bad = t[:-2] + ("a" if t[-2] != "a" else "b") + t[-1]
    with pytest.raises(Exception):
        bamb5.decode(bad)
