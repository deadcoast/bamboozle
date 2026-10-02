import json

import pytest

from workshop.mcp import workshop_mcp
from workshop.scrape import alias_pass, bamb5, lookup


def test_both_taxonomy_shapes_resolve_weighted_aliases(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical.json"
    categorized = tmp_path / "categorized.json"
    canonical.write_text(
        json.dumps(
            {"canonical": {"red hair": {"category": "anatomy", "aliases": ["redhead"]}}}
        ),
        encoding="utf-8",
    )
    categorized.write_text(
        json.dumps({"categories": {"anatomy": {"red hair": ["redhead"]}}}),
        encoding="utf-8",
    )
    for path in (canonical, categorized):
        tax = alias_pass.Taxonomy.load(path)
        assert tax.resolve("(Redhead:1.2)") == "red hair"
        for module in (workshop_mcp, bamb5):
            monkeypatch.setattr(module, "TAXONOMY", path)
            assert module.normalize_triggers(["(Redhead:1.2)"]) == {
                "tags": {"anatomy": ["red hair"]},
                "unknown": [],
            }


def test_canonical_term_cannot_overwrite_existing_alias(tmp_path):
    path = tmp_path / "taxonomy.json"
    path.write_text(
        json.dumps(
            {
                "canonical": {
                    "red hair": {"aliases": ["redhead"]},
                    "redhead": {"aliases": []},
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="collides"):
        alias_pass.Taxonomy.load(path)


def test_plaintext_alias_pass_needs_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(bamb5, "KEY_PATH", tmp_path / "absent.key")
    tax = alias_pass.Taxonomy({"redhead": "red hair"}, {"red hair": "anatomy"}, set())
    assert alias_pass.run({"A": ["redhead"]}, tax).canon_by_lora == {"A": {"red hair"}}
    assert not bamb5.KEY_PATH.exists()


def test_workshop_script_dependency_is_explicitly_unavailable(tmp_path, monkeypatch):
    for module in (workshop_mcp, bamb5):
        monkeypatch.setattr(module, "SCRAPE", tmp_path)
        assert module.promote_lora("sample")["status"] == "unavailable"
        with pytest.raises(ValueError, match="Unsupported"):
            module._run("../unexpected.py")


def test_rotating_bamb5_key_preserves_recovery_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(bamb5, "KEY_PATH", tmp_path / "key.key")
    bamb5.init_key()
    original = bamb5.KEY_PATH.read_bytes()
    bamb5.init_key(force=True)
    assert bamb5.KEY_PATH.read_bytes() != original
    backups = list(tmp_path.glob("key.key.*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_lookup_fingerprint_tracks_bytes_not_only_stat_metadata(tmp_path):
    import os

    path = tmp_path / "data.json"
    path.write_bytes(b"one")
    stat = path.stat()
    before = lookup._fingerprint(path)
    path.write_bytes(b"two")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert lookup._fingerprint(path) != before
