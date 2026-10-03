import json

import pytest

from workshop.mcp import workshop_mcp as server
from workshop.scrape import bamb5, lookup
from workshop.scrape.alias_pass import Taxonomy


@pytest.fixture
def index():
    tax = Taxonomy({"redhead": "red hair"}, {"red hair": "anatomy"}, {"masterpiece"})
    return lookup.Index(
        {"red hair": ["A", "B"], "neon": ["B"]},
        {"A": ["red hair"], "B": ["red hair", "neon"]},
        tax,
        "fixture",
    )


def test_index_cache_reuses_rebuilds_and_forces(monkeypatch, index):
    fingerprint = ["fixture"]
    calls = []

    def load(cls, *paths, force=False, content=None):
        calls.append(force)
        return lookup.Index(index.by_canon, index.by_lora, index.tax, fingerprint[0])

    monkeypatch.setattr(server, "_IDX", None)
    monkeypatch.setattr(lookup, "_inputs", lambda *paths: {})
    monkeypatch.setattr(lookup, "_content_fingerprint", lambda content: fingerprint[0])
    monkeypatch.setattr(lookup.Index, "load", classmethod(load))

    first = server._idx()
    assert server._idx() is first
    assert calls == [False]
    fingerprint[0] = "changed"
    refreshed = server._idx()
    assert refreshed is not first and refreshed.fp == "changed"
    assert server._idx(force=True) is not refreshed
    assert calls == [False, False, True]


def test_lookup_tools_and_stack_overlap(tmp_path, monkeypatch, index):
    monkeypatch.setattr(server, "_idx", lambda force=False: index)
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps({"pairs": {"A|B": {"score": 0.8}}}), encoding="utf-8")
    monkeypatch.setattr(server, "MATRIX", matrix)

    assert server.who_uses("redhead") == {
        "canon": "red hair",
        "category": "anatomy",
        "known": True,
        "loras": ["A", "B"],
    }
    assert server.who_uses("masterpiece")["error"] == "ignored or undecodable"
    assert server.lora_who("redhead") == index.who("redhead")
    assert server.lora_suggest("redhead") == index.suggest("redhead", 5)
    result = server.stack_check(["B", "A", "missing"])
    assert result["missing"] == ["missing"]
    assert result["ok"] is False
    assert result["pairs"] == [
        {
            "a": "B",
            "b": "A",
            "overlap": 0.5,
            "shared": ["red hair"],
            "flag": True,
            "matrix": 0.8,
        }
    ]


@pytest.mark.parametrize("module", [server, bamb5])
def test_manifest_query_merges_fields_and_filters(tmp_path, monkeypatch, module):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "loras": {
                    "A": {
                        "categories": ["style"],
                        "tags": ["grain"],
                        "base_model": "test",
                    },
                    "B": {
                        "categories": ["anatomy"],
                        "tags": ["red hair"],
                        "base_model": "other",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "MANIFEST", manifest)

    assert module.query_manifest(category="style", tag="grain", base_model="test") == [
        {
            "name": "A",
            "categories": ["style"],
            "tags": ["grain"],
            "base_model": "test",
        }
    ]
    assert module.query_manifest(tag="absent") == []
