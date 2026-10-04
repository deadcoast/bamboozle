"""Exercise diagnosis and explicit recovery with retained synthetic failure states."""

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
from workshop.pipeline import run_script
from workshop.scrape.audit_matrix import audit

SOURCE_ROOT = Path(__file__).resolve().parents[1]


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file() and path.suffix != ".key"
    }


def qualify(root: Path, report: dict) -> None:
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError(
            "Recovery qualification requires a new or empty directory"
        )
    root.mkdir(parents=True, exist_ok=True)

    def run(workshop: Path, *args: str, code: int = 0) -> dict:
        result = run_script(
            workshop / "scrape", Path(sys.executable), "promote.py", *args
        )
        report["checks"].append(
            {"workshop": workshop.name, "arguments": args, "execution": result}
        )
        assert result["returncode"] == code and not result["stdout_truncated"], result
        return json.loads(result["stdout"])

    for case in ("promotion", "quarantine", "audit", "conflict", "corrupt"):
        workshop = root / case
        initialize(workshop)
        examples.candidate(workshop, "ExampleA", ["red hair"])
        if case in {"quarantine", "audit"}:
            run(workshop, "--candidate", "candidates/ExampleA.json")
        prepared = None
        if case != "audit":
            prepared = (
                plans.prepare_quarantine(
                    workshop, "ExampleA", "synthetic recovery qualification"
                )
                if case == "quarantine"
                else plans.prepare_promotion(workshop, "candidates/ExampleA.json")
            )
        writer = ops.atomic_write

        def fail(
            path: Path,
            data: bytes,
            *,
            case=case,
            workshop=workshop,
            writer=writer,
            **kwargs,
        ) -> None:
            if (case == "quarantine" and path.name.endswith(".done.json")) or (
                case != "quarantine" and path == workshop / ops.LEDGER
            ):
                raise OSError("Controlled recovery diagnostic fixture interruption")
            writer(path, data, **kwargs)

        operation_id = ""
        with patch.object(ops, "atomic_write", fail):
            try:
                if prepared is None:
                    audit(workshop, apply=True)
                else:
                    plans.apply_plan(
                        workshop,
                        prepared["operation_id"],
                        prepared["plan_sha256"],
                        dry_run=False,
                    )
            except ops.PendingOperation as exc:
                operation_id = exc.operation_id
        assert operation_id, "Failure injection did not leave a pending journal"
        if case == "conflict":
            for path in (workshop / ops.LEDGER, workshop / "prod/model-ExampleA.bin"):
                path.write_bytes(b"retained synthetic conflicting bytes")
        elif case == "corrupt":
            (workshop / f"scrape/prepared/{operation_id}.json").write_bytes(
                b"{damaged fixture"
            )
        before = snapshot(workshop)
        diagnosis = run(workshop, "--diagnose-recovery", operation_id)
        expected = case if case in {"conflict", "corrupt"} else "recoverable"
        assert diagnosis["status"] == expected, diagnosis
        assert diagnosis["recovery_ready"] == (expected == "recoverable")
        assert snapshot(workshop) == before
        if expected == "recoverable":
            run(workshop, "--recover", operation_id, "--dry-run")
            assert snapshot(workshop) == before
            saved = run(workshop, "--recover", operation_id)
            assert saved["journal_sha256"] == diagnosis["records"]["journal"]["sha256"]
            assert (
                run(workshop, "--diagnose-recovery", operation_id)["status"]
                == "already_completed"
            )
            rows = list(
                csv.DictReader(io.StringIO((workshop / ops.LEDGER).read_text()))
            )
            assert sum(row["operation_id"] == operation_id for row in rows) == 1
            assert (
                run(workshop, "--recover", operation_id)["status"]
                == "already_completed"
            )
        else:
            if case == "conflict":
                conflicts = [
                    row
                    for row in diagnosis["controls"] + diagnosis["assets"]
                    if row["state"] == "conflict"
                ]
                assert len(conflicts) == 2
            run(workshop, "--recover", operation_id, code=1)
            assert snapshot(workshop) == before
        report["cases"].append(
            {
                "case": case,
                "operation_id": operation_id,
                "observed_status": expected,
                "read_only": True,
                "final_file_sha256": snapshot(workshop),
            }
        )
    report.update(status="passed", conflict_and_corrupt_fixtures_retained=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workshop", type=Path, required=True)
    parser.add_argument(
        "--report",
        type=Path,
        help="New nested JSON path; default uses managed current/archive storage",
    )
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    replace_report = args.report is None
    try:
        destination = report_destination(
            project, "VIGILES_RECOVERY_QUALIFICATION.json", args.report
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
        "cases": [],
    }
    try:
        report["source_sha256"] = {
            name: sha256((SOURCE_ROOT / name).read_bytes())
            for name in (
                "scripts/qualify_recovery.py",
                "src/workshop/recovery.py",
                "src/workshop/operations.py",
                "src/workshop/plans.py",
                "src/workshop/scrape/promote.py",
                "src/workshop/mcp/workshop_mcp.py",
                "src/workshop/scrape/bamb5.py",
            )
        }
        qualify(root, report)
    except (OSError, ValueError, TypeError, KeyError, AssertionError) as exc:
        report.update(status="failed", error=str(exc))
    write_report(project, destination, report, replace=replace_report)
    print(json.dumps({"status": report["status"], "report": str(destination)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
