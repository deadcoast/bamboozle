import base64
import csv
import io
import json
import sys
from pathlib import Path

import pytest

from bamboozle.utils import PublicationError, atomic_write, sha256
from workshop import examples, plans
from workshop import operations as ops
from workshop.bootstrap import initialize
from workshop.mcp import workshop_mcp
from workshop.scrape import bamb5, promote
from workshop.scrape.audit_matrix import audit


@pytest.fixture
def native(tmp_path):
    root = tmp_path / "native workshop"
    initialize(root)
    for name in ("ExampleA", "ExampleB"):
        examples.candidate(root, name, ["red hair"])
    return root


def files(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def prepare(root):
    return plans.prepare_promotion(root, "candidates/ExampleA.json")


def apply(root, report, **kwargs):
    return plans.apply_plan(
        root, report["operation_id"], report["plan_sha256"], **kwargs
    )


def test_prepare_review_apply_uses_exact_bytes_once(native):
    before = files(native)
    report = prepare(native)
    saved = files(native)
    assert all(saved[name] == content for name, content in before.items())
    assert set(saved) - set(before) == {report["plan_file"], "scrape/.vigiles.lock"}
    assert ops.pipeline_status(native)["readable"]
    details = plans.inspect_plan(native, report["operation_id"], details=True)
    assert details["applicable"]
    manifest = next(
        change for change in details["changes"] if change["path"] == ops.MANIFEST
    )
    assert "ExampleA" in json.loads(manifest["after"])["loras"]
    assert apply(native, report)["status"] == "plan_preview"
    assert files(native) == saved
    result = apply(native, report, dry_run=False)
    journal = native / f"scrape/transactions/{report['operation_id']}.json"
    assert journal.read_bytes() == saved[report["plan_file"]]
    assert result["journal_sha256"] == report["plan_sha256"]
    assert (native / report["asset"]["destination"]).read_bytes() == before[
        report["asset"]["source"]
    ]
    assert plans.inspect_plan(native, report["operation_id"])["status"] == "applied"
    completed = files(native)
    for dry_run in (True, False):
        with pytest.raises(ValueError, match="applied"):
            apply(native, report, dry_run=dry_run)
    assert files(native) == completed


def test_prepared_quarantine_retains_reason_and_exact_destination(native):
    for name in ("ExampleA", "ExampleB"):
        ops.promote(native, f"candidates/{name}.json", dry_run=False)
    audit(native, apply=True)
    before = files(native)
    report = plans.prepare_quarantine(native, "ExampleA", "operator fixture review")
    assert report["reason"] == "operator fixture review"
    details = plans.inspect_plan(native, report["operation_id"], details=True)
    matrix = next(item for item in details["changes"] if item["path"] == ops.MATRIX)
    assert "ExampleA|ExampleB" in json.loads(matrix["before"])["pairs"]
    assert json.loads(matrix["after"])["pairs"] == {}
    assert not (native / report["asset"]["destination"]).exists()
    apply(native, report, dry_run=False)
    assert not (native / report["asset"]["source"]).exists()
    assert (native / report["asset"]["destination"]).read_bytes() == before[
        report["asset"]["source"]
    ]
    ledger = list(csv.DictReader(io.StringIO((native / ops.LEDGER).read_text())))
    assert ledger[-1]["reason"] == report["reason"]


@pytest.mark.parametrize(
    "path",
    [
        "candidates/ExampleA.json",
        "candidates/ExampleA.validation.json",
        "candidates/ExampleA.bin",
        *sorted(ops.CONTROLS),
    ],
)
def test_changed_preconditions_fail_before_journal_or_asset(native, path):
    report = prepare(native)
    target = native / path
    target.write_bytes(target.read_bytes() + b" ")
    before = files(native)
    assert not plans.inspect_plan(native, report["operation_id"])["applicable"]
    for dry_run in (True, False):
        with pytest.raises(ValueError, match="changed"):
            apply(native, report, dry_run=dry_run)
    assert files(native) == before
    assert not list((native / "scrape/transactions").iterdir())


def test_identical_destination_and_after_image_are_not_fresh_state(native):
    report = prepare(native)
    destination = native / report["asset"]["destination"]
    destination.write_bytes((native / report["asset"]["source"]).read_bytes())
    with pytest.raises(FileExistsError, match="already exists"):
        apply(native, report, dry_run=False)
    destination.unlink()
    plan = json.loads((native / report["plan_file"]).read_bytes())
    item = next(item for item in plan["writes"] if item["path"] == ops.MANIFEST)
    (native / ops.MANIFEST).write_bytes(base64.b64decode(item["after"]))
    before = files(native)
    with pytest.raises(ValueError, match="Control file changed"):
        apply(native, report, dry_run=False)
    assert files(native) == before


def test_digest_and_target_workshop_are_bound(native, tmp_path):
    report = prepare(native)
    for digest in ("0" * 64, "invalid", report["plan_sha256"].upper()):
        with pytest.raises(ValueError, match="SHA-256"):
            plans.apply_plan(native, report["operation_id"], digest, dry_run=False)
    path = native / report["plan_file"]
    raw = path.read_bytes()
    path.write_bytes(raw + b"\n")
    with pytest.raises(ValueError, match="reviewed SHA-256"):
        apply(native, report, dry_run=False)
    path.write_bytes(raw)
    other = tmp_path / "other"
    initialize(other)
    (other / report["plan_file"]).write_bytes(raw)
    with pytest.raises(ValueError, match="different target workshop"):
        apply(other, report, dry_run=False)


@pytest.mark.parametrize("action", ["promote", "quarantine"])
@pytest.mark.parametrize("failure", ["ledger", "completion"])
def test_interruption_requires_explicit_recovery_of_reviewed_bytes(
    native, monkeypatch, action, failure
):
    if action == "quarantine":
        ops.promote(native, "candidates/ExampleA.json", dry_run=False)
        report = plans.prepare_quarantine(native, "ExampleA", "fixture")
    else:
        report = prepare(native)
    operation_id = report["operation_id"]

    def fail(path, content, **kwargs):
        if (failure == "ledger" and path == native / ops.LEDGER) or (
            failure == "completion" and path.name.endswith(".done.json")
        ):
            raise OSError("injected publication failure")
        atomic_write(path, content, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ops, "atomic_write", fail)
        with pytest.raises(ops.PendingOperation) as error:
            apply(native, report, dry_run=False)
    assert error.value.operation_id == operation_id
    assert plans.inspect_plan(native, operation_id)["status"] == "recovery_required"
    before = files(native)
    with pytest.raises(ValueError, match="recovery_required"):
        apply(native, report, dry_run=False)
    with pytest.raises(ValueError, match="Cannot cancel"):
        plans.cancel_plan(native, operation_id, report["plan_sha256"])
    assert ops.recover(native, operation_id)["status"] == "recovery_preview"
    assert files(native) == before
    # A corrupt recovery journal must not replace the reviewed operation.
    journal = native / f"scrape/transactions/{operation_id}.json"
    raw = journal.read_bytes()
    journal.write_bytes(raw + b"\n")
    corrupt = files(native)
    with pytest.raises(ValueError, match="differs from the reviewed"):
        ops.recover(native, operation_id, dry_run=False)
    assert files(native) == corrupt
    journal.write_bytes(raw)
    assert (
        ops.recover(native, operation_id, dry_run=False)["journal_sha256"]
        == report["plan_sha256"]
    )
    assert (
        ops.recover(native, operation_id, dry_run=False)["status"]
        == "already_completed"
    )
    rows = list(csv.DictReader(io.StringIO((native / ops.LEDGER).read_text())))
    assert sum(row["operation_id"] == operation_id for row in rows) == 1


def test_cancellation_frees_bounded_slots_without_deleting_evidence(
    native, monkeypatch
):
    monkeypatch.setattr(plans, "MAX_PENDING_PLANS", 2)
    first, second = prepare(native), prepare(native)
    before = files(native)
    with pytest.raises(ValueError, match="limit reached"):
        prepare(native)
    assert files(native) == before
    cancelled = plans.cancel_plan(native, first["operation_id"], first["plan_sha256"])
    assert cancelled["status"] == "cancelled"
    assert (
        plans.cancel_plan(native, first["operation_id"], first["plan_sha256"])
        == cancelled
    )
    with pytest.raises(ValueError, match="cancelled"):
        apply(native, first, dry_run=False)
    third = prepare(native)
    assert (native / first["plan_file"]).exists()
    apply(native, second, dry_run=False)
    assert not plans.inspect_plan(native, third["operation_id"])["applicable"]
    with pytest.raises(ValueError, match="Cannot cancel"):
        plans.cancel_plan(native, second["operation_id"], second["plan_sha256"])


def test_writer_lock_serializes_apply(native):
    report = prepare(native)
    with (
        ops._lock(ops.workspace(native)),
        pytest.raises(OSError, match="Another native workshop operation"),
    ):
        apply(native, report, dry_run=False)
    assert not list((native / "scrape/transactions").iterdir())
    assert apply(native, report, dry_run=False)["status"] == "saved"


def test_old_workshops_prepare_lazily_and_report_publication_cleanup_failure(
    native, monkeypatch, capsys
):
    (native / plans.DIRECTORY).rmdir()

    def cleanup_failure(path, content, **kwargs):
        atomic_write(path, content, **kwargs)
        raise PublicationError("injected cleanup failure")

    monkeypatch.setattr(plans, "atomic_write", cleanup_failure)
    assert (
        promote.main(["--prepare-candidate", "candidates/ExampleA.json"], root=native)
        == 1
    )
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "prepared_cleanup_failed"
    assert sha256((native / report["plan_file"]).read_bytes()) == report["plan_sha256"]
    assert plans.inspect_plan(native, report["operation_id"])["applicable"]
    assert not list((native / "prod").iterdir())


def test_response_limit_is_checked_before_persistence(native, monkeypatch):
    monkeypatch.setattr(plans, "MAX_SUMMARY_BYTES", 100)
    before = files(native)
    with pytest.raises(ValueError, match="response limit"):
        prepare(native)
    assert files(native) == before


@pytest.mark.parametrize(
    "arguments",
    [
        ["--apply-plan", "0" * 32],
        ["--candidate", "candidates/ExampleA.json", "--plan-sha256", "0" * 64],
        ["--prepare-candidate", "candidates/ExampleA.json", "--dry-run"],
        ["--status", "--details"],
        ["--show-plan", "0" * 32, "--reason", "unused"],
    ],
)
def test_cli_rejects_ambiguous_plan_options(native, arguments):
    before = files(native)
    with pytest.raises(SystemExit) as error:
        promote.main(arguments, root=native)
    assert error.value.code == 2
    assert files(native) == before


@pytest.mark.parametrize("module", [workshop_mcp, bamb5])
def test_real_plan_cli_through_both_mcp_adapters(native, monkeypatch, module):
    monkeypatch.setattr(module, "SCRAPE", native / "scrape")
    monkeypatch.setattr(module, "PY", Path(sys.executable))

    def result(report):
        assert report["returncode"] == 0, report
        assert not report["stdout_truncated"]
        return json.loads(report["stdout"])

    report = result(module.prepare_promotion("candidates/ExampleA.json"))
    identity = report["operation_id"], report["plan_sha256"]
    assert result(module.inspect_plan(identity[0]))["applicable"]
    assert result(module.apply_plan(*identity))["status"] == "plan_preview"
    assert (
        result(module.apply_plan(*identity, dry_run=False))["journal_sha256"]
        == identity[1]
    )
    assert module.apply_plan(*identity, dry_run=False)["returncode"] == 1
    quarantine = result(module.prepare_quarantine("ExampleA", "review fixture"))
    assert (
        result(
            module.cancel_plan(quarantine["operation_id"], quarantine["plan_sha256"])
        )["status"]
        == "cancelled"
    )
    assert (native / "prod/model-ExampleA.bin").exists()
