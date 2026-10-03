"""Retain exact-plan CLI and recovery evidence using synthetic assets only."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

from bamboozle import __version__
from bamboozle.evidence import report_destination, write_report
from bamboozle.utils import sha256, utc_now
from workshop import examples, plans
from workshop import operations as ops
from workshop.bootstrap import initialize
from workshop.pipeline import inspect_pipeline, run_script


def snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): sha256(p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and p.suffix != ".key"
    }


def qualify(root: Path, report: dict) -> None:
    initialize(root)
    examples.candidate(root, "ExampleA", ["red hair"])
    checks = report["checks"]

    def run(*args: str, code: int = 0) -> dict:
        result = run_script(root / "scrape", Path(sys.executable), "promote.py", *args)
        checks.append({"arguments": args, "execution": result})
        assert result["returncode"] == code and not result["stdout_truncated"], result
        return json.loads(result["stdout"])

    before = snapshot(root)
    prepared = run("--prepare-candidate", "candidates/ExampleA.json")
    identity = prepared["operation_id"], prepared["plan_sha256"]
    report["reviewed_promotion"] = prepared
    after = snapshot(root)
    assert all(after[path] == digest for path, digest in before.items())
    assert set(after) - set(before) == {prepared["plan_file"], "scrape/.vigiles.lock"}
    assert run("--show-plan", identity[0])["applicable"]
    run("--apply-plan", identity[0], "--plan-sha256", identity[1], "--dry-run")
    assert snapshot(root) == after
    assert inspect_pipeline(root / "scrape", Path(sys.executable))["ok"]
    source = root / prepared["asset"]["source"]
    original = source.read_bytes()
    source.write_bytes(original + b"changed")
    stale = snapshot(root)
    assert (
        "changed"
        in run("--apply-plan", identity[0], "--plan-sha256", identity[1], code=1)[
            "error"
        ]
    )
    assert snapshot(root) == stale
    source.write_bytes(original)
    writer = ops.atomic_write

    def fail_ledger(path: Path, data: bytes, **kwargs) -> None:
        if path == root / ops.LEDGER:
            raise OSError("Controlled prepared-plan ledger failure")
        writer(path, data, **kwargs)

    with patch.object(ops, "atomic_write", fail_ledger):
        try:
            plans.apply_plan(root, *identity, dry_run=False)
        except ops.PendingOperation as exc:
            report["controlled_failure"] = {
                "operation_id": exc.operation_id,
                "error": str(exc),
            }
    assert report["controlled_failure"]["operation_id"] == identity[0]
    assert run("--show-plan", identity[0])["status"] == "recovery_required"
    run("--apply-plan", identity[0], "--plan-sha256", identity[1], code=1)
    pending = snapshot(root)
    run("--recover", identity[0], "--dry-run")
    assert snapshot(root) == pending
    recovered = run("--recover", identity[0])
    assert recovered["journal_sha256"] == identity[1]
    assert (root / f"scrape/transactions/{identity[0]}.json").read_bytes() == (
        root / prepared["plan_file"]
    ).read_bytes()
    completed = snapshot(root)
    assert (
        "applied"
        in run("--apply-plan", identity[0], "--plan-sha256", identity[1], code=1)[
            "error"
        ]
    )
    assert snapshot(root) == completed
    rows = list(csv.DictReader(io.StringIO((root / ops.LEDGER).read_text())))
    assert sum(row["operation_id"] == identity[0] for row in rows) == 1
    unused = run("--prepare-quarantine", "ExampleA", "--reason", "cancel fixture")
    run("--cancel-plan", unused["operation_id"], "--plan-sha256", unused["plan_sha256"])
    assert (root / "prod/model-ExampleA.bin").read_bytes() == original
    quarantine = run(
        "--prepare-quarantine", "ExampleA", "--reason", "synthetic qualification"
    )
    report["reviewed_quarantine"] = quarantine
    saved = run(
        "--apply-plan",
        quarantine["operation_id"],
        "--plan-sha256",
        quarantine["plan_sha256"],
    )
    assert saved["journal_sha256"] == quarantine["plan_sha256"]
    assert (root / quarantine["asset"]["destination"]).read_bytes() == original
    assert not (root / "prod/model-ExampleA.bin").exists()
    readiness = inspect_pipeline(root / "scrape", Path(sys.executable))
    assert readiness["ok"] and ops.pipeline_status(root)["pending_operations"] == []
    report.update(
        status="passed",
        exact_journal_bytes=True,
        stale_rejected=True,
        replay_rejected=True,
        recovery_ledger_rows=1,
        cancellation_preserved_asset=True,
        readiness=readiness,
        final_file_sha256=snapshot(root),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workshop", type=Path, required=True)
    parser.add_argument(
        "--report",
        type=Path,
        help="New JSON report path; default is the managed reports/current directory",
    )
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    replace_report = args.report is None
    try:
        args.report = report_destination(
            project, "VIGILES_PLAN_QUALIFICATION.json", args.report
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    root = args.workshop.absolute()
    report = {
        "schema_version": 1,
        "created": utc_now(),
        "version": __version__,
        "workshop": str(root),
        "fixture_only": True,
        "production_model_qualified": False,
        "status": "running",
        "checks": [],
    }
    try:
        qualify(root, report)
    except (OSError, ValueError, TypeError, KeyError, AssertionError) as exc:
        report.update(status="failed", error=str(exc))
    write_report(project, args.report, report, replace=replace_report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(args.report),
                "workshop": str(root),
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
