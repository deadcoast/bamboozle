import base64
import csv
import io
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESSIV

from bamboozle.utils import atomic_write, json_bytes
from workshop import examples
from workshop import operations as ops
from workshop.bootstrap import initialize
from workshop.mcp import workshop_mcp
from workshop.pipeline import inspect_pipeline, run_script
from workshop.scrape import bamb5
from workshop.scrape.audit_matrix import audit


@pytest.fixture
def native(tmp_path):
    root = tmp_path / "native workshop"
    initialize(root)
    examples.candidate(root, "ExampleA", ["red hair"])
    examples.candidate(root, "ExampleB", ["red hair", "film grain"])
    examples.candidate(root, "ExampleC", ["neon"])
    return root


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def load(root, name):
    return json.loads((root / name).read_bytes())


def promote(root, name="ExampleA", **kwargs):
    return ops.promote(root, f"candidates/{name}.json", **kwargs)


def test_promotion_dry_run_is_read_only_and_apply_preserves_source(native):
    before = snapshot(native)
    assert promote(native)["status"] == "preview"
    assert snapshot(native) == before
    saved = promote(native, dry_run=False)
    assert saved["status"] == "saved" and not saved["model_qualified"]
    entry = load(native, ops.MANIFEST)["loras"]["ExampleA"]
    assert (native / entry["path"]).read_bytes() == before["candidates/ExampleA.bin"]
    assert (native / "candidates/ExampleA.bin").read_bytes() == before[
        "candidates/ExampleA.bin"
    ]
    assert ops.pipeline_status(native)["status"] == "saved"
    assert (
        ops.recover(native, saved["operation_id"], dry_run=False)["status"]
        == "already_completed"
    )
    with pytest.raises(FileExistsError):
        promote(native, dry_run=False)


@pytest.mark.parametrize("action", ["promote", "quarantine", "audit"])
def test_partial_publication_is_preserved_and_resumed_once(native, monkeypatch, action):
    if action != "promote":
        promote(native, dry_run=False)
    original = (native / ops.LEDGER).read_bytes()

    def fail_ledger(path, content, **kwargs):
        if path == native / ops.LEDGER:
            raise OSError("injected ledger failure")
        atomic_write(path, content, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ops, "atomic_write", fail_ledger)
        with pytest.raises(ops.PendingOperation) as failure:
            if action == "promote":
                promote(native, dry_run=False)
            elif action == "quarantine":
                ops.quarantine(native, "ExampleA", "fixture review", dry_run=False)
            else:
                audit(native, apply=True)
    operation_id = failure.value.operation_id
    assert ops.pipeline_status(native)["status"] == "recovery_required"
    assert not inspect_pipeline(native / "scrape", Path(sys.executable))["ok"]
    with pytest.raises(ValueError, match="pending"):
        promote(native, "ExampleB", dry_run=False)
    before = snapshot(native)
    assert ops.recover(native, operation_id)["status"] == "recovery_preview"
    assert snapshot(native) == before
    assert ops.recover(native, operation_id, dry_run=False)["status"] == "saved"
    assert ops.pipeline_status(native)["pending_operations"] == []
    rows = list(csv.DictReader(io.StringIO((native / ops.LEDGER).read_text())))
    assert sum(row["operation_id"] == operation_id for row in rows) == 1
    journal = load(native, f"scrape/transactions/{operation_id}.json")
    ledger = next(item for item in journal["writes"] if item["path"] == ops.LEDGER)
    assert base64.b64decode(ledger["before_content"]) == original
    if action == "quarantine":
        assert not (native / "prod/model-ExampleA.bin").exists()
        assert len(list((native / "quarantine").rglob("*.bin"))) == 1
    assert (native / "candidates/ExampleA.bin").exists()


def test_recovery_refuses_unrelated_metadata_changes(native, monkeypatch):
    def fail_ledger(path, content, **kwargs):
        if path == native / ops.LEDGER:
            raise OSError("fixture")
        atomic_write(path, content, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ops, "atomic_write", fail_ledger)
        with pytest.raises(ops.PendingOperation) as error:
            promote(native, dry_run=False)
    (native / ops.MANIFEST).write_bytes(b'{"loras": {"Unrelated": {}}}')
    before = snapshot(native)
    with pytest.raises(ValueError, match="Control file changed"):
        ops.recover(native, error.value.operation_id, dry_run=False)
    assert snapshot(native) == before


def test_quarantine_preserves_bytes_and_removes_active_pair(native):
    promote(native, dry_run=False)
    promote(native, "ExampleB", dry_run=False)
    audit(native, apply=True)
    original = (native / "prod/model-ExampleA.bin").read_bytes()
    before = snapshot(native)
    assert ops.quarantine(native, "ExampleA", "fixture")["status"] == "preview"
    assert snapshot(native) == before
    saved = ops.quarantine(native, "ExampleA", "fixture", dry_run=False)
    assert "ExampleA" not in load(native, ops.MANIFEST)["loras"]
    assert load(native, ops.MATRIX)["pairs"] == {}
    assert next((native / "quarantine").rglob("*.bin")).read_bytes() == original
    journal = load(native, f"scrape/transactions/{saved['operation_id']}.json")
    matrix = next(item for item in journal["writes"] if item["path"] == ops.MATRIX)
    assert (
        "ExampleA|ExampleB"
        in json.loads(base64.b64decode(matrix["before_content"]))["pairs"]
    )


def test_audit_recomputes_zero_overlap_preserving_scores_and_prune_history(native):
    promote(native, dry_run=False)
    promote(native, "ExampleC", dry_run=False)
    matrix = {
        "pairs": {
            "ExampleA|ExampleC": {
                "score": 0.9,
                "notes": "retained",
                "trigger_overlap": 1,
                "trigger_conflict": True,
            },
            "ExampleA|Missing": {"score": 0.7},
        }
    }
    (native / ops.MATRIX).write_bytes(json_bytes(matrix))
    before = snapshot(native)
    report = audit(native, prune=True)
    assert report["dangling_pairs"] == ["ExampleA|Missing"]
    assert snapshot(native) == before
    with pytest.raises(ValueError, match="explicit --prune"):
        audit(native, apply=True)
    audit(native, prune=True, apply=True)
    assert load(native, ops.MATRIX)["pairs"] == {
        "ExampleA|ExampleC": {
            "score": 0.9,
            "notes": "retained",
            "trigger_overlap": 0.0,
            "trigger_conflict": False,
        }
    }


def test_audit_uses_workspace_key_and_fails_before_writing_on_decode_error(native):
    key = AESSIV.generate_key(512)
    (native / "scrape/_bamb5.key").write_bytes(key)
    metadata = load(native, "candidates/ExampleA.json")
    metadata["triggers"] = [bamb5.encode("red hair", AESSIV(key))]
    (native / "candidates/ExampleA.json").write_bytes(json_bytes(metadata))
    promote(native, dry_run=False)
    assert audit(native)["status"] == "preview"
    (native / "scrape/_bamb5.key").write_bytes(AESSIV.generate_key(512))
    before = snapshot(native)
    with pytest.raises(ValueError, match="Undecodable"):
        audit(native, apply=True)
    assert snapshot(native) == before


@pytest.mark.parametrize("field", ["artifact", "sha256", "validation"])
def test_invalid_candidate_is_rejected_without_writes(native, field):
    metadata = load(native, "candidates/ExampleA.json")
    metadata[field] = "../outside.bin" if field != "sha256" else "0" * 64
    (native / "candidates/ExampleA.json").write_bytes(json_bytes(metadata))
    before = snapshot(native)
    with pytest.raises(ValueError):
        promote(native, dry_run=False)
    assert snapshot(native) == before


def test_existing_destination_is_never_overwritten(native, monkeypatch):
    copy = ops._asset_copy

    def competing_writer(source, destination, digest):
        destination.write_bytes(b"unrelated writer")
        copy(source, destination, digest)

    monkeypatch.setattr(ops, "_asset_copy", competing_writer)
    with pytest.raises(ops.PendingOperation):
        promote(native, dry_run=False)
    assert (native / "prod/model-ExampleA.bin").read_bytes() == b"unrelated writer"


def test_lock_excludes_another_writer(native):
    with (
        ops._lock(ops.workspace(native)),
        pytest.raises(OSError, match="Another native"),
    ):
        promote(native, dry_run=False)
    assert not list((native / "prod").iterdir())


def test_observed_manifest_cannot_be_replaced_by_a_stale_plan(native, monkeypatch):
    execute = ops._execute

    def concurrent_edit(ws, plan, apply):
        (native / ops.MANIFEST).write_bytes(b'{"loras": {"Concurrent": {}}}')
        return execute(ws, plan, apply)

    monkeypatch.setattr(ops, "_execute", concurrent_edit)
    with pytest.raises(ValueError, match="Control file changed"):
        promote(native, dry_run=False)
    assert "Concurrent" in load(native, ops.MANIFEST)["loras"]
    assert not list((native / "prod").iterdir())


def test_bootstrap_refuses_existing_files(tmp_path):
    keep = tmp_path / "keep.txt"
    keep.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        initialize(tmp_path)
    assert keep.read_bytes() == b"keep"


def test_completion_marker_is_bound_to_the_journal(native):
    saved = promote(native, dry_run=False)
    journal = native / "scrape/transactions" / f"{saved['operation_id']}.json"
    journal.write_bytes(journal.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Completion record"):
        ops.pipeline_status(native)
    assert not inspect_pipeline(native / "scrape", Path(sys.executable))["ok"]


@pytest.mark.parametrize(
    "change", [{"passed": False}, {"kind": "unknown"}, {"artifact_sha256": "0" * 64}]
)
def test_failed_or_mismatched_validation_prevents_promotion(native, change):
    path = native / "candidates/ExampleA.validation.json"
    record = json.loads(path.read_bytes())
    record.update(change)
    path.write_bytes(json_bytes(record))
    before = snapshot(native)
    with pytest.raises(ValueError, match="passing validation"):
        promote(native, dry_run=False)
    assert snapshot(native) == before


def test_quarantine_never_deletes_a_concurrently_changed_production_file(
    native, monkeypatch
):
    promote(native, dry_run=False)
    source = native / "prod/model-ExampleA.bin"
    original = source.read_bytes()

    def concurrent_edit(path, content, **kwargs):
        atomic_write(path, content, **kwargs)
        if path == native / ops.MANIFEST:
            source.write_bytes(b"concurrent replacement")

    monkeypatch.setattr(ops, "atomic_write", concurrent_edit)
    with pytest.raises(ops.PendingOperation):
        ops.quarantine(native, "ExampleA", "fixture", dry_run=False)
    assert source.read_bytes() == b"concurrent replacement"
    assert next((native / "quarantine").rglob("*.bin")).read_bytes() == original


def test_quarantine_recovers_after_removal_before_completion_marker(
    native, monkeypatch
):
    promote(native, dry_run=False)

    def fail_completion(path, content, **kwargs):
        if path.name.endswith(".done.json"):
            raise OSError("injected completion marker failure")
        atomic_write(path, content, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ops, "atomic_write", fail_completion)
        with pytest.raises(ops.PendingOperation) as error:
            ops.quarantine(native, "ExampleA", "fixture", dry_run=False)
    assert not (native / "prod/model-ExampleA.bin").exists()
    ledger = (native / ops.LEDGER).read_bytes()
    ops.recover(native, error.value.operation_id, dry_run=False)
    assert (native / ops.LEDGER).read_bytes() == ledger
    assert ops.pipeline_status(native)["status"] == "saved"


@pytest.mark.parametrize("module", [workshop_mcp, bamb5])
def test_real_scripts_through_both_adapters(native, monkeypatch, module):
    monkeypatch.setattr(module, "SCRAPE", native / "scrape")
    monkeypatch.setattr(module, "PY", Path(sys.executable))
    assert inspect_pipeline(native / "scrape", Path(sys.executable))["ok"]
    report = module.promote_lora("candidates/ExampleA.json")
    assert json.loads(report["stdout"])["status"] == "preview"
    assert (
        module.promote_lora("candidates/ExampleA.json", dry_run=False)["returncode"]
        == 0
    )
    assert module.run_audit(apply=True)["returncode"] == 0
    assert (
        json.loads(
            module.quarantine_lora("ExampleA", "fixture", dry_run=True)["stdout"]
        )["status"]
        == "preview"
    )
    assert module.quarantine_lora("ExampleA", "fixture")["returncode"] == 0
    result = run_script(
        native / "scrape", Path(sys.executable), "promote.py", "--status"
    )
    assert json.loads(result["stdout"])["status"] == "saved"
