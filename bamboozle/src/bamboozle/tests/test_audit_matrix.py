import json

import pytest

from bamboozle.audit_matrix import update_trigger_overlap
from workshop.scrape import bamb5
from workshop.scrape.alias_pass import Taxonomy


@pytest.mark.parametrize(("threshold", "conflict"), [(0.5, True), (0.75, False)])
def test_trigger_overlap_preserves_matrix_metadata(
    tmp_path, monkeypatch, threshold, conflict
):
    monkeypatch.setattr(bamb5, "KEY_PATH", tmp_path / "key")
    bamb5.init_key()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "loras": {
                    "B": {"triggers": ["red hair", "neon"]},
                    "A": {"trained_words": [bamb5.encode("redhead")]},
                }
            }
        ),
        encoding="utf-8",
    )
    tax = Taxonomy({"redhead": "red hair"}, {"red hair": "anatomy"}, set())
    matrix = {"A|B": {"score": 0.8, "notes": "keep"}, "C|D": {"score": 0.2}}

    result = update_trigger_overlap(matrix, manifest, tax, threshold)

    assert result is matrix
    assert result["A|B"] == {
        "score": 0.8,
        "notes": "keep",
        "trigger_overlap": 0.5,
        "trigger_conflict": conflict,
    }
    assert result["C|D"] == {"score": 0.2}
