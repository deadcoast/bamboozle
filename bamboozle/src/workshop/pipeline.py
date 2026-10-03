"""External workshop dependency inspection and shared subprocess reporting.

Inspection never executes a script. A successful subprocess exit establishes only
process completion; the external pipeline owns publication and recovery semantics.
"""

from __future__ import annotations

import ast
import math
import os
import subprocess
from pathlib import Path
from typing import Any

from bamboozle.utils import parse_json, sha256, utc_now
from bamboozle.workspace import Workspace

SCRIPTS = frozenset({"promote.py", "audit_matrix.py"})


def _script(scrape: Path, name: str) -> tuple[Path, str]:
    workspace = Workspace(scrape)
    path = workspace.resolve(name)
    content = workspace.read(path)
    ast.parse(content, filename=name)
    return path, sha256(content)


def _tail(value: bytes | str | None, limit: int) -> tuple[str, bool]:
    data = value.encode("utf-8") if isinstance(value, str) else value or b""
    return data[-limit:].decode("utf-8", errors="replace"), len(data) > limit


def run_script(
    scrape: Path,
    python: Path,
    script: str,
    *args: str,
    timeout: float = 900,
) -> dict[str, Any]:
    """Invoke an allowlisted script once, retaining explicit failure semantics."""
    if script not in SCRIPTS:
        raise ValueError("Unsupported workshop script")
    if not math.isfinite(timeout) or not 0 < timeout <= 900:
        raise ValueError("Workshop timeout must be greater than zero and at most 900")
    if len(args) > 32 or any(
        not isinstance(arg, str) or "\0" in arg or len(arg) > 8192 for arg in args
    ):
        raise ValueError("Invalid workshop arguments")
    report: dict[str, Any] = {
        "schema_version": 1,
        "script": script,
        "started": False,
        "side_effects_possible": False,
        "deployment_qualified": False,
        "retry_performed": False,
    }
    try:
        path, digest = _script(scrape, script)
        # Preserve venv interpreter symlinks: resolving them selects the base Python.
        interpreter = python.absolute()
        if not interpreter.is_file():
            raise FileNotFoundError(f"Workshop interpreter unavailable: {interpreter}")
    except (OSError, ValueError, SyntaxError) as exc:
        report.update(
            returncode=127,
            stdout="",
            stderr=f"Workshop dependency unavailable: {script}: {exc}",
            status="unavailable",
        )
        return report
    report["script_sha256"] = digest
    try:
        result = subprocess.run(
            [str(interpreter), str(path), *args],
            cwd=path.parent,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
        status = "completed" if code == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        code, stdout, stderr = 124, exc.stdout or b"", exc.stderr or b""
        status = "timed_out"
    except OSError as exc:
        report.update(returncode=126, stdout="", stderr=str(exc), status="start_failed")
        return report
    output, output_truncated = _tail(stdout, 8000)
    errors, errors_truncated = _tail(stderr, 4000)
    report.update(
        returncode=code,
        stdout=output,
        stderr=errors,
        stdout_truncated=output_truncated,
        stderr_truncated=errors_truncated,
        status=status,
        started=True,
        # Includes dry-run: an external flag is a request, not enforced isolation.
        side_effects_possible=True,
    )
    if status in {"failed", "timed_out"}:
        report["next_action"] = (
            "Inspect external outputs and its ledger before retrying; "
            "the adapter neither rolls back files nor proves publication success."
        )
    if status == "timed_out":
        report["termination_scope"] = "direct_child_only"
    return report


def _manifest(data: Any) -> set[str]:
    if not isinstance(data, dict) or not isinstance(data.get("loras"), dict):
        raise TypeError("Manifest requires an object named loras")
    for name, entry in data["loras"].items():
        if not name.strip() or "|" in name or not isinstance(entry, dict):
            raise ValueError("Manifest requires nonempty LoRA IDs and object entries")
        for field in ("tags", "categories", "triggers", "trained_words"):
            value = entry.get(field, [])
            if not isinstance(value, list) or any(
                not isinstance(term, str) for term in value
            ):
                raise ValueError(f"Manifest {field} must be a list of strings")
        if "base_model" in entry and not isinstance(entry["base_model"], str):
            raise ValueError("Manifest base_model must be a string")
    return set(data["loras"])


def _matrix(data: Any, loras: set[str] | None) -> int:
    if not isinstance(data, dict) or not isinstance(data.get("pairs"), dict):
        raise TypeError("Matrix requires an object named pairs")
    seen: set[tuple[str, ...]] = set()
    for pair, entry in data["pairs"].items():
        names = pair.split("|")
        normalized = tuple(sorted(names))
        if (
            len(names) != 2
            or not all(name.strip() for name in names)
            or names[0] == names[1]
            or names != sorted(names)
            or not isinstance(entry, dict)
            or normalized in seen
        ):
            raise ValueError("Matrix requires unique pairs of distinct LoRA IDs")
        if loras is not None and not set(names) <= loras:
            raise ValueError("Matrix references LoRA IDs absent from the manifest")
        seen.add(normalized)
    return len(seen)


def inspect_pipeline(scrape: Path, python: Path) -> dict[str, Any]:
    """Read bounded inputs without importing scripts or creating workshop state."""
    checks: list[dict[str, Any]] = []
    workspace: Workspace | None = None
    try:
        workspace = Workspace(scrape)
    except (OSError, ValueError) as exc:
        checks.append({"name": "scrape_directory", "ok": False, "error": str(exc)})
    checks.append(
        {"name": "python", "ok": python.is_file(), "path": str(python.absolute())}
    )
    loras: set[str] | None = None
    for name in (*sorted(SCRIPTS), "_lora_manifest.json", "_compatibility_matrix.json"):
        check: dict[str, Any] = {"name": name, "ok": False}
        try:
            if workspace is None:
                raise FileNotFoundError("Workshop scrape directory is unavailable")
            content = workspace.read(name)
            check["sha256"] = sha256(content)
            if name in SCRIPTS:
                ast.parse(content, filename=name)
                check["execution_contract"] = "unverified"
            elif name == "_lora_manifest.json":
                loras = _manifest(parse_json(content))
                check["loras"] = len(loras)
            else:
                check["pairs"] = _matrix(parse_json(content), loras)
            check["ok"] = True
        except (OSError, ValueError, TypeError, SyntaxError) as exc:
            check["error"] = str(exc)
        checks.append(check)
    native = (scrape.parent / "_vigiles_workshop.json").exists()
    if native:
        from bamboozle import __version__
        from workshop.operations import pipeline_status
        from workshop.operations import workspace as native_workspace

        check = {"name": "native_workshop", "ok": False, "version": __version__}
        try:
            state = pipeline_status(scrape.parent)
            key = native_workspace(scrape.parent).read("scrape/_bamb5.key")
            check["pending_operations"] = state["pending_operations"]
            check["key_valid_length"] = len(key) == 64
            check["ok"] = not state["pending_operations"] and len(key) == 64
        except (OSError, ValueError, TypeError, KeyError) as exc:
            check["error"] = str(exc)
        checks.append(check)
    ok = all(check["ok"] for check in checks)
    return {
        "schema_version": 1,
        "gate": "VIG-DEPLOY-01",
        "backend": "native" if native else "external",
        "created": utc_now(),
        "status": "ready_for_review" if ok else "needs_attention",
        "ok": ok,
        "scrape": str(scrape.absolute()),
        "executed_scripts": False,
        "deployment_qualified": False,
        "checks": checks,
        "next_action": (
            "Review script behavior and qualify dry-run, publication, and recovery "
            "against isolated copies before any live operation."
            if ok
            else "Supply or repair the failed dependencies, then repeat workshop-check."
        ),
    }
