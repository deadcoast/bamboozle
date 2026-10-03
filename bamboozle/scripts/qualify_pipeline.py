"""Qualify native file operations with explicit synthetic assets, never real models."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

from bamboozle import __version__
from bamboozle.utils import json_bytes, sha256, utc_now
from workshop import examples
from workshop import operations as ops
from workshop.bootstrap import initialize
from workshop.pipeline import inspect_pipeline, run_script
from workshop.snapshot import capture


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }


def qualify(root: Path) -> dict:
    initialize(root)
    for name, triggers in [
        ("ExampleA", ["red hair"]),
        ("ExampleB", ["red hair", "film grain"]),
        ("ExampleC", ["neon"]),
    ]:
        examples.candidate(root, name, triggers)
    checks: list[dict] = []

    def run(script: str, *args: str) -> dict:
        result = run_script(root / "scrape", Path(sys.executable), script, *args)
        if result["returncode"] != 0:
            raise ValueError(f"Native script failed: {result}")
        response = json.loads(result["stdout"])
        checks.append({"script": script, "arguments": args, "result": response})
        return response

    before = snapshot(root)
    run("promote.py", "--candidate", "candidates/ExampleA.json", "--dry-run")
    assert snapshot(root) == before, "Promotion preview mutated the workspace"
    for name in ("ExampleA", "ExampleB"):
        run("promote.py", "--candidate", f"candidates/{name}.json")
    matrix = {
        "schema_version": 1,
        "pairs": {
            "ExampleA|ExampleB": {
                "score": 0.85,
                "notes": "synthetic score; not model evaluation",
            },
            "ExampleA|Missing": {"score": 0.1, "notes": "prune fixture"},
        },
    }
    (root / ops.MATRIX).write_bytes(json_bytes(matrix))
    before = snapshot(root)
    run("audit_matrix.py", "--prune")
    assert snapshot(root) == before, "Audit preview mutated the workspace"
    run("audit_matrix.py", "--prune", "--apply")
    writer = ops.atomic_write

    def fail_after_asset(path: Path, data: bytes, **kwargs) -> None:
        if path == root / ops.LEDGER:
            raise OSError("Controlled qualification fault after publication")
        writer(path, data, **kwargs)

    operation_id = ""
    with patch.object(ops, "atomic_write", fail_after_asset):
        try:
            ops.promote(root, "candidates/ExampleC.json", dry_run=False)
        except ops.PendingOperation as exc:
            operation_id = exc.operation_id
    assert operation_id, "Fault injection did not produce a pending operation"
    assert ops.pipeline_status(root)["status"] == "recovery_required"
    before = snapshot(root)
    blocked_reads = []
    for name in (ops.MANIFEST, ops.MATRIX, ops.LEDGER):
        path = root / name
        try:
            capture(root / "scrape", [path])
        except ValueError as exc:
            assert "pending" in str(exc)
            blocked_reads.append(name)
    assert len(blocked_reads) == 3, "Incomplete native state was exposed to a reader"
    assert snapshot(root) == before, "Rejected reads mutated the workshop"
    run("promote.py", "--recover", operation_id, "--dry-run")
    assert snapshot(root) == before, "Recovery preview mutated the workspace"
    run("promote.py", "--recover", operation_id)
    before = snapshot(root)
    run(
        "promote.py",
        "--quarantine",
        "ExampleC",
        "--reason",
        "synthetic qualification",
        "--dry-run",
    )
    assert snapshot(root) == before, "Quarantine preview mutated the workspace"
    run("promote.py", "--quarantine", "ExampleC", "--reason", "synthetic qualification")
    assert not (root / "prod/model-ExampleC.bin").exists()
    assert (
        next((root / "quarantine").rglob("*.bin")).read_bytes()
        == (root / "candidates/ExampleC.bin").read_bytes()
    )
    manifest = json.loads((root / ops.MANIFEST).read_bytes())
    matrix = json.loads((root / ops.MATRIX).read_bytes())
    assert set(manifest["loras"]) == {"ExampleA", "ExampleB"}
    pair = matrix["pairs"]["ExampleA|ExampleB"]
    assert pair["score"] == 0.85 and pair["trigger_overlap"] == 0.5
    assert ops.pipeline_status(root)["pending_operations"] == []
    readiness = inspect_pipeline(root / "scrape", Path(sys.executable))
    assert readiness["ok"]
    capture(root / "scrape", [root / ops.MANIFEST, root / ops.MATRIX])
    return {
        "status": "passed",
        "checks": checks,
        "controlled_failure_recovered": operation_id,
        "pending_read_rejections": blocked_reads,
        "completed_snapshot_after_recovery": True,
        "manifest": manifest,
        "matrix": matrix,
        "readiness": readiness,
        "final_file_sha256": {
            name: digest
            for name, digest in snapshot(root).items()
            if not name.endswith(".key")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workshop", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Use a new report path to preserve earlier qualification evidence")
    root = args.workshop.absolute()
    report = {
        "schema_version": 1,
        "created": utc_now(),
        "version": __version__,
        "workshop": str(root),
        "fixture_only": True,
        "production_model_qualified": False,
    }
    try:
        report.update(qualify(root))
    except (OSError, ValueError, TypeError, KeyError, AssertionError) as exc:
        report.update(status="failed", error=str(exc))
    args.report.write_bytes(json_bytes(report))
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
