import json

import pytest

from bamboozle.dictionary import Dictionary


def test_bundled_dictionary_is_valid():
    dictionary = Dictionary.load()
    assert len(dictionary) == 110
    assert len(dictionary.fingerprint) == 64


@pytest.mark.parametrize(
    "entries",
    [
        {"alpha": "beta", "gamma": "beta"},
        {"alpha": "beta", "beta": "gamma"},
        {" alpha": "beta"},
        {"alpha": ""},
        {"Alpha": "beta"},
        {"alpha": "beta\nx"},
        {"alpha": "__scm_lit_beta"},
        {"straße": "first", "strasse": "second"},
    ],
)
def test_ambiguous_dictionary_rejected(entries):
    with pytest.raises(ValueError):
        Dictionary(entries)


def test_dictionary_snapshot_roundtrip_and_immutability(tmp_path):
    entries = {"alpha": "beta", "gamma": "delta"}
    dictionary = Dictionary(entries, "release-2")
    entries["alpha"] = "changed"
    path = tmp_path / "dictionary.json"
    dictionary.save(path)
    loaded = Dictionary.load(path)
    assert dict(loaded) == {"alpha": "beta", "gamma": "delta"}
    assert loaded.version == "release-2"
    assert loaded.fingerprint == dictionary.fingerprint
    assert (
        Dictionary(dict(reversed(list(loaded.items()))), "release-2").fingerprint
        == loaded.fingerprint
    )
    with pytest.raises(FileExistsError):
        dictionary.save(path)


def test_duplicate_dictionary_json_keys_rejected(tmp_path):
    path = tmp_path / "mapping.json"
    path.write_text('{"alpha":"beta","alpha":"gamma"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        Dictionary.load(path)


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": True, "mapping_version": "1", "entries": {}},
        {"schema_version": 2, "mapping_version": "1", "entries": {}},
        {"schema_version": 1, "mapping_version": "1", "entries": {}, "surprise": 1},
    ],
)
def test_dictionary_unknown_schema_rejected(tmp_path, document):
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError):
        Dictionary.load(path)
