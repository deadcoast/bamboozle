"""Exercise bounded inventory through real CLI calls in a synthetic workshop."""

from __future__ import annotations

import argparse
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
from workshop.pipeline import run_script


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

    def run(*arguments: str, code: int = 0) -> dict:
        before = snapshot(root)
        result = run_script(
            root / "scrape",
            Path(sys.executable),
            "promote.py",
            "--list-plans",
            *arguments,
        )
        checks.append({"arguments": arguments, "execution": result})
        assert result["returncode"] == code and not result["stdout_truncated"], result
        assert snapshot(root) == before, "Inventory changed files"
        return json.loads(result["stdout"])

    assert run()["items"] == []
    stale = plans.prepare_promotion(root, "candidates/ExampleA.json")
    applied = plans.prepare_promotion(root, "candidates/ExampleA.json")
    plans.apply_plan(
        root, applied["operation_id"], applied["plan_sha256"], dry_run=False
    )
    cancelled = plans.prepare_quarantine(root, "ExampleA", "cancelled fixture")
    plans.cancel_plan(root, cancelled["operation_id"], cancelled["plan_sha256"])
    prepared = plans.prepare_quarantine(root, "ExampleA", "prepared fixture")
    rows = []
    cursor = None
    while True:
        page = run("--limit", "2", *(["--cursor", cursor] if cursor else []))
        assert len(json.dumps(page, indent=2).encode("ascii")) + 1 <= 6000
        rows.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert len(rows) < 10, "Inventory did not finish paging"
    states = {item["operation_id"]: item["status"] for item in rows}
    assert states == {
        stale["operation_id"]: "stale",
        applied["operation_id"]: "applied",
        cancelled["operation_id"]: "cancelled",
        prepared["operation_id"]: "prepared",
    }
    first = run("--limit", "1")
    corrupt = root / plans.DIRECTORY / ("0" * 32 + ".json")
    corrupt.write_bytes(b"{")
    assert "restart listing" in run("--cursor", first["next_cursor"], code=1)["error"]
    assert run("--limit", "1")["items"][0]["status"] == "corrupt"
    writer = ops.atomic_write

    def fail(path: Path, data: bytes, **kwargs) -> None:
        if path == root / ops.LEDGER:
            raise OSError("Controlled inventory recovery fixture")
        writer(path, data, **kwargs)

    with patch.object(ops, "atomic_write", fail):
        try:
            plans.apply_plan(
                root, prepared["operation_id"], prepared["plan_sha256"], dry_run=False
            )
        except ops.PendingOperation as exc:
            report["controlled_failure"] = str(exc)
    assert report.get("controlled_failure")
    pending = run("--limit", "10")
    assert (
        next(
            item
            for item in pending["items"]
            if item.get("operation_id") == prepared["operation_id"]
        )["status"]
        == "recovery_required"
    )
    ops.recover(root, prepared["operation_id"], dry_run=False)
    final = run("--limit", "10")
    assert (
        next(
            item
            for item in final["items"]
            if item.get("operation_id") == prepared["operation_id"]
        )["status"]
        == "applied"
    )
    report.update(
        status="passed",
        paged_states=states,
        corrupt_record_retained=True,
        changed_catalog_cursor_rejected=True,
        pending_recovery_discovered=True,
        read_only=True,
        metadata_only=True,
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
            project, "VIGILES_INVENTORY_QUALIFICATION.json", args.report
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    root = args.workshop.absolute()
    report = {
        "schema_version": 1,
        "version": __version__,
        "created": utc_now(),
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
    print(json.dumps({"status": report["status"], "report": str(args.report)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
