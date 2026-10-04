import base64
import csv
import io
import json
import sys
from pathlib import Path

import pytest

from bamboozle.utils import atomic_write, json_bytes, sha256
from workshop import examples, plans, recovery
from workshop import operations as ops
from workshop.bootstrap import initialize
from workshop.mcp import workshop_mcp
from workshop.scrape import bamb5, promote
from workshop.scrape.audit_matrix import audit


@pytest.fixture
def native(tmp_path):
    root = tmp_path / "native workshop"
    initialize(root)
    examples.candidate(root, "ExampleA", ["red hair"])
    examples.candidate(root, "ExampleB", ["film grain"])
    return root


def files(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None
        for p in root.rglob("*")
    }


def interrupt(root, monkeypatch, action="promote", stage="ledger", prepared=True):
    if action != "promote":
        ops.promote(root, "candidates/ExampleA.json", dry_run=False)
    if prepared and action != "audit":
        review = (
            plans.prepare_promotion(root, "candidates/ExampleA.json")
            if action == "promote"
            else plans.prepare_quarantine(root, "ExampleA", "fixture")
        )
        run = lambda: plans.apply_plan(
            root, review["operation_id"], review["plan_sha256"], dry_run=False
        )
    else:
        run = (
            (lambda: ops.promote(root, "candidates/ExampleA.json", dry_run=False))
            if action == "promote"
            else (lambda: ops.quarantine(root, "ExampleA", "fixture", dry_run=False))
            if action == "quarantine"
            else lambda: audit(root, apply=True)
        )

    def fail_write(path, content, **kwargs):
        if (stage == "ledger" and path == root / ops.LEDGER) or (
            stage == "completion" and path.name.endswith(".done.json")
        ):
            raise OSError("controlled diagnosis interruption")
        atomic_write(path, content, **kwargs)

    def fail_finish(ws, plan):
        raise ops.PendingOperation(
            plan["operation_id"], OSError("before asset publication")
        )

    with monkeypatch.context() as patch:
        patch.setattr(ops, "atomic_write", fail_write)
        if stage == "journal":
            patch.setattr(ops, "_finish", fail_finish)
        with pytest.raises(ops.PendingOperation) as failure:
            run()
    return failure.value.operation_id


def inspect(root, operation_id):
    before = files(root)
    report = ops.diagnose_recovery(root, operation_id)
    assert files(root) == before
    assert report["read_only"]
    assert (
        len(json.dumps(report, indent=2).encode("ascii")) + 1
        <= recovery.MAX_RESPONSE_BYTES
    )
    return report


@pytest.mark.parametrize(
    "action,prepared",
    [
        ("promote", True),
        ("quarantine", True),
        ("promote", False),
        ("quarantine", False),
        ("audit", False),
    ],
)
@pytest.mark.parametrize("stage", ["journal", "ledger", "completion"])
def test_diagnosis_agrees_with_real_explicit_recovery(
    native, monkeypatch, action, prepared, stage
):
    operation_id = interrupt(native, monkeypatch, action, stage, prepared)
    report = inspect(native, operation_id)
    assert report["status"] == "recoverable", report
    assert report["recovery_ready"] and report["inspection_complete"]
    assert report["current_state_checked"] and not report["writer_lock_checked"]
    assert report["transactions"]["blocking_count"] == 0
    controls = {row["path"]: row["state"] for row in report["controls"]}
    assert controls[ops.LEDGER] == ("matching" if stage == "completion" else "pending")
    if action != "audit":
        source, destination = report["assets"]
        assert source["state"] == (
            "removed"
            if action == "quarantine" and stage == "completion"
            else "matching"
        )
        assert destination["state"] == ("pending" if stage == "journal" else "matching")
    journal = native / f"scrape/transactions/{operation_id}.json"
    raw = journal.read_bytes()
    assert ops.recover(native, operation_id)["status"] == "recovery_preview"
    assert ops.recover(native, operation_id, dry_run=False)["journal_sha256"] == sha256(
        raw
    )
    assert journal.read_bytes() == raw
    rows = list(csv.DictReader(io.StringIO((native / ops.LEDGER).read_text())))
    assert sum(row["operation_id"] == operation_id for row in rows) == 1
    completed = inspect(native, operation_id)
    assert (
        completed["status"] == "already_completed" and not completed["recovery_ready"]
    )
    assert not completed["current_state_checked"]


def test_all_conflicts_are_reported_without_exposing_file_bodies(native, monkeypatch):
    operation_id = interrupt(native, monkeypatch)
    changed = [
        "candidates/ExampleA.json",
        ops.MATRIX,
        ops.MANIFEST,
        ops.LEDGER,
        "candidates/ExampleA.bin",
        "prod/model-ExampleA.bin",
    ]
    for name in changed:
        (native / name).write_bytes(b"private changed content must not be returned")
    report = inspect(native, operation_id)
    assert report["status"] == "conflict" and not report["recovery_ready"]
    rows = report["inputs"] + report["controls"] + report["assets"]
    assert {r["path"] for r in rows if r["state"] == "conflict"} == set(changed)
    assert "private changed content" not in json.dumps(report)
    with pytest.raises(ValueError, match="input changed"):
        ops.recover(native, operation_id, dry_run=False)


@pytest.mark.parametrize(
    "damage",
    [
        "json",
        "array",
        "missing_write",
        "before_digest",
        "asset",
        "inputs",
        "action",
        "lora",
        "created",
        "reason",
    ],
)
def test_corrupt_journal_is_not_a_recoverable_interruption(native, monkeypatch, damage):
    operation_id = interrupt(native, monkeypatch, prepared=False)
    journal = native / f"scrape/transactions/{operation_id}.json"
    plan = json.loads(journal.read_bytes())
    if damage == "missing_write":
        plan["writes"].pop()
    elif damage == "before_digest":
        plan["writes"][0]["before_content"] = base64.b64encode(b"other").decode()
    elif damage == "asset":
        plan["asset"] = {}
    elif damage == "inputs":
        plan["inputs"] = {ops.MATRIX: "invalid"}
    elif damage == "action":
        plan["action"] = []
    elif damage in {"lora", "created", "reason"}:
        del plan[damage]
    journal.write_bytes(
        b"{" if damage == "json" else b"[]" if damage == "array" else json_bytes(plan)
    )
    report = inspect(native, operation_id)
    assert report["status"] == "corrupt" and not report["recovery_ready"]
    assert report["records"]["journal"]["state"] == "corrupt"
    with pytest.raises((ValueError, TypeError)):
        ops.recover(native, operation_id, dry_run=False)


@pytest.mark.parametrize(
    "record",
    [
        "prepared",
        "completion",
        "missing_prepared",
        "mismatched_plan",
        "cancellation",
        "missing_journal",
    ],
)
def test_retained_evidence_damage_blocks_diagnostics_and_recovery(
    native, monkeypatch, record
):
    operation_id = interrupt(native, monkeypatch)
    journal = native / f"scrape/transactions/{operation_id}.json"
    prepared = native / f"scrape/prepared/{operation_id}.json"
    if record == "prepared":
        prepared.write_bytes(b"{")
    elif record == "missing_prepared":
        prepared.unlink()
    elif record == "mismatched_plan":
        prepared.write_bytes(prepared.read_bytes() + b" ")
    elif record == "completion":
        journal.with_suffix(".done.json").write_bytes(b'{"status":"saved"}')
    elif record == "cancellation":
        prepared.with_suffix(".cancelled.json").write_bytes(
            json_bytes(
                {
                    "status": "cancelled",
                    "operation_id": operation_id,
                    "plan_sha256": sha256(prepared.read_bytes()),
                }
            )
        )
    else:
        journal.with_suffix(".done.json").write_bytes(
            json_bytes(
                {
                    "status": "saved",
                    "operation_id": operation_id,
                    "journal_sha256": sha256(journal.read_bytes()),
                }
            )
        )
        journal.unlink()
    report = inspect(native, operation_id)
    assert report["status"] == "corrupt" and not report["recovery_ready"], report
    with pytest.raises((OSError, ValueError, TypeError)):
        ops.recover(native, operation_id, dry_run=False)


def test_completed_evidence_does_not_assert_historical_files_are_current(native):
    first = ops.promote(native, "candidates/ExampleA.json", dry_run=False)
    ops.quarantine(native, "ExampleA", "later valid operation", dry_run=False)
    report = inspect(native, first["operation_id"])
    assert report["status"] == "already_completed"
    assert not report["current_state_checked"] and report["assets"] == []


def test_unused_cancelled_and_unknown_ids_do_not_create_state(native):
    (native / "scrape/prepared").rmdir()
    assert inspect(native, "0" * 32)["status"] == "not_found"
    assert not (native / "scrape/prepared").exists()
    review = plans.prepare_promotion(native, "candidates/ExampleA.json")
    assert inspect(native, review["operation_id"])["status"] == "not_started"
    plans.cancel_plan(native, review["operation_id"], review["plan_sha256"])
    assert inspect(native, review["operation_id"])["status"] == "cancelled"


def test_other_pending_and_bad_completions_block_readiness(native, monkeypatch):
    operation_id = interrupt(native, monkeypatch)
    other = "a" * 32
    (native / f"scrape/transactions/{other}.json").write_bytes(b"{}")
    report = inspect(native, operation_id)
    assert report["status"] == "conflict"
    assert report["transactions"]["blockers"] == [
        {"operation_id": other, "state": "pending"}
    ]
    with pytest.raises(ValueError, match="additional pending"):
        ops.recover(native, operation_id, dry_run=False)
    (native / f"scrape/transactions/{other}.done.json").write_bytes(b"{}")
    report = inspect(native, operation_id)
    assert report["status"] == "conflict"
    assert report["transactions"]["blockers"][0]["state"] == "corrupt"


@pytest.mark.parametrize("resource", ["metadata", "asset", "directory", "response"])
def test_bounds_return_explicit_unavailable_without_false_readiness(
    native, monkeypatch, resource
):
    operation_id = interrupt(native, monkeypatch)
    if resource == "metadata":
        monkeypatch.setattr(recovery, "MAX_METADATA_BYTES", 100)
    elif resource == "asset":
        monkeypatch.setattr(ops, "MAX_ASSET_BYTES", 1)
    elif resource == "directory":
        monkeypatch.setattr(recovery, "MAX_DIRECTORY_ENTRIES", 0)
    else:
        monkeypatch.setattr(recovery, "MAX_RESPONSE_BYTES", 1000)
    report = inspect(native, operation_id)
    assert report["status"] == "unavailable" and not report["recovery_ready"]
    assert not report["inspection_complete"]
    if resource == "metadata":
        assert report["metadata_bytes_read"] <= 100


@pytest.mark.parametrize("name", [ops.LEDGER, "candidates/ExampleA.bin"])
def test_missing_required_files_are_conflicts(native, monkeypatch, name):
    operation_id = interrupt(native, monkeypatch)
    (native / name).unlink()
    report = inspect(native, operation_id)
    assert report["status"] == "conflict" and not report["recovery_ready"]


@pytest.mark.parametrize("kind", ["metadata", "asset", "absent_completion", "catalog"])
def test_detected_concurrent_change_never_reports_recoverable(
    native, monkeypatch, kind
):
    operation_id = interrupt(native, monkeypatch)
    current = recovery._current

    def changing(ws, plan, decoded, report):
        current(ws, plan, decoded, report)
        if kind == "metadata":
            (native / ops.LEDGER).write_bytes(b"changed during inspection")
        elif kind == "asset":
            (native / "prod/model-ExampleA.bin").write_bytes(
                b"changed during inspection"
            )
        elif kind == "absent_completion":
            (native / f"scrape/transactions/{operation_id}.done.json").write_bytes(
                b"{}"
            )
        else:
            (native / "scrape/transactions/new.tmp").write_bytes(b"another writer")

    monkeypatch.setattr(recovery, "_current", changing)
    report = ops.diagnose_recovery(native, operation_id)
    assert report["status"] == "changing" and not report["recovery_ready"]


@pytest.mark.parametrize("module", [workshop_mcp, bamb5])
def test_real_cli_diagnosis_through_both_adapters(native, monkeypatch, module):
    operation_id = interrupt(native, monkeypatch)
    monkeypatch.setattr(module, "SCRAPE", native / "scrape")
    monkeypatch.setattr(module, "PY", Path(sys.executable))
    before = files(native)
    result = module.diagnose_recovery(operation_id)
    assert result["returncode"] == 0 and not result["stdout_truncated"]
    assert json.loads(result["stdout"])["status"] == "recoverable"
    assert files(native) == before


@pytest.mark.parametrize(
    "args",
    [
        ["--dry-run"],
        ["--details"],
        ["--limit", "1"],
        ["--reason", "x"],
        ["--recover", "0" * 32],
    ],
)
def test_diagnostic_cli_rejects_ambiguous_options(native, args):
    with pytest.raises(SystemExit) as error:
        promote.main(["--diagnose-recovery", "0" * 32, *args], root=native)
    assert error.value.code == 2


@pytest.mark.parametrize("kind", ["directory", "access", "escape"])
def test_unreadable_or_unsafe_paths_never_authorize_recovery(native, monkeypatch, kind):
    operation_id = interrupt(native, monkeypatch, prepared=False)
    if kind == "directory":
        path = native / ops.LEDGER
        path.unlink()
        path.mkdir()
    elif kind == "access":
        read = recovery._Reader.read

        def denied(self, name):
            if str(name) == ops.LEDGER:
                raise PermissionError("Controlled inaccessible ledger")
            return read(self, name)

        monkeypatch.setattr(recovery._Reader, "read", denied)
    else:
        path = native / f"scrape/transactions/{operation_id}.json"
        document = json.loads(path.read_bytes())
        document["asset"]["source"] = "../outside.bin"
        path.write_bytes(json_bytes(document))
    report = inspect(native, operation_id)
    assert report["status"] == "unavailable" and not report["recovery_ready"]


def test_completed_journal_still_requires_valid_structure(native):
    saved = ops.promote(native, "candidates/ExampleA.json", dry_run=False)
    operation_id = saved["operation_id"]
    path = native / f"scrape/transactions/{operation_id}.json"
    document = json.loads(path.read_bytes())
    del document["lora"]
    raw = json_bytes(document)
    path.write_bytes(raw)
    saved["journal_sha256"] = sha256(raw)
    path.with_suffix(".done.json").write_bytes(json_bytes(saved))
    report = inspect(native, operation_id)
    assert report["status"] == "corrupt"
    with pytest.raises(ValueError, match="Unsupported"):
        ops.recover(native, operation_id, dry_run=False)


def test_long_paths_cannot_silently_truncate_a_report(native, monkeypatch):
    operation_id = interrupt(native, monkeypatch, prepared=False)
    path = native / f"scrape/transactions/{operation_id}.json"
    document = json.loads(path.read_bytes())
    document["inputs"]["candidates/" + "界" * 1000 + ".json"] = "0" * 64
    path.write_bytes(json_bytes(document))
    report = inspect(native, operation_id)
    assert report["status"] == "unavailable"
    assert report["required_response_bytes"] > report["response_limit_bytes"]
    assert not report["inspection_complete"] and not report["recovery_ready"]


def test_metadata_budget_includes_marker_and_allows_exact_boundary(native, monkeypatch):
    marker = native / "_vigiles_workshop.json"
    monkeypatch.setattr(recovery, "MAX_METADATA_BYTES", marker.stat().st_size)
    report = inspect(native, "0" * 32)
    assert report["status"] == "not_found"
    assert report["metadata_bytes_read"] == marker.stat().st_size


@pytest.mark.parametrize("operation_id", ["../x", "X" * 32, "0" * 31, "0" * 33])
def test_invalid_operation_ids_are_rejected_before_file_access(native, operation_id):
    before = files(native)
    with pytest.raises(ValueError, match="Invalid operation ID"):
        ops.diagnose_recovery(native, operation_id)
    assert files(native) == before
