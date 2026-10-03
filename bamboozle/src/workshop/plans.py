"""Durable, digest-bound promotion/quarantine plans; preparation publishes no assets."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from bamboozle.utils import (
    PublicationError,
    atomic_write,
    json_bytes,
    parse_json,
    sha256,
)
from bamboozle.workspace import Workspace
from workshop import operations as ops

DIRECTORY = "scrape/prepared"
MAX_PENDING_PLANS = 32
MAX_SUMMARY_BYTES = 6000


class PreparedPlanSaved(OSError):
    """The plan was published, but temporary-file cleanup failed."""

    def __init__(self, report: dict[str, Any], cause: OSError):
        super().__init__(str(cause))
        self.report = {
            **report,
            "status": "prepared_cleanup_failed",
            "error": str(cause),
        }


def _path(ws: Workspace, plan_id: str, *, cancelled: bool = False) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", plan_id) is None:
        raise ValueError("Invalid prepared plan ID")
    suffix = ".cancelled.json" if cancelled else ".json"
    return ws.resolve(f"{DIRECTORY}/{plan_id}{suffix}", write=True)


def _load(
    ws: Workspace, plan_id: str, expected_sha256: str | None = None
) -> tuple[dict[str, Any], bytes]:
    raw = ws.read(_path(ws, plan_id))
    if expected_sha256 is not None:
        if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise ValueError(
                "Expected plan SHA-256 must be 64 lowercase hex characters"
            )
        if sha256(raw) != expected_sha256:
            raise ValueError("Prepared plan digest does not match the reviewed SHA-256")
    plan = parse_json(raw)
    if (
        not isinstance(plan, dict)
        or plan.get("kind") != "vigiles.workshop.operation"
        or type(plan.get("schema_version")) is not int
        or plan["schema_version"] != 1
        or plan.get("operation_id") != plan_id
        or plan.get("action") not in {"promote", "quarantine"}
        or not isinstance(plan.get("prepared_for"), str)
        or Path(plan["prepared_for"]) != ws.root
        or not isinstance(plan.get("reason"), str)
        or not isinstance(plan.get("asset"), dict)
        or not isinstance(plan.get("inputs"), dict)
        or not isinstance(plan.get("writes"), list)
        or not all(isinstance(item, dict) for item in plan["writes"])
    ):
        raise ValueError("Unsupported prepared plan or different target workshop")
    paths = [item.get("path") for item in plan["writes"]]
    required = {ops.MANIFEST, ops.LEDGER, ops.STATUS}
    if plan["action"] == "quarantine":
        required.add(ops.MATRIX)
    if (
        any(not isinstance(path, str) for path in paths)
        or set(paths) != required
        or len(paths) != len(required)
    ):
        raise ValueError("Prepared plan has unexpected control writes")
    if not (ops.CONTROLS - required) <= plan["inputs"].keys():
        raise ValueError("Prepared plan is missing control preconditions")
    ops._validate_plan(ws, plan, plan_id)
    return plan, raw


def _state(ws: Workspace, plan_id: str, digest: str) -> str:
    journal = ws.resolve(f"scrape/transactions/{plan_id}.json")
    done = ws.resolve(f"scrape/transactions/{plan_id}.done.json")
    if journal.exists() or done.exists():
        if sha256(ws.read(journal)) != digest:
            raise ValueError(
                "Operation journal differs from the reviewed prepared plan"
            )
        return "applied" if ops._completed(ws, plan_id) else "recovery_required"
    cancelled = _path(ws, plan_id, cancelled=True)
    if cancelled.exists():
        record = parse_json(ws.read(cancelled))
        if record != {
            "status": "cancelled",
            "operation_id": plan_id,
            "plan_sha256": digest,
        }:
            raise ValueError("Cancellation record does not match the prepared plan")
        return "cancelled"
    return "prepared"


def _ready(ws: Workspace, plan: dict[str, Any], raw: bytes) -> None:
    state = _state(ws, plan["operation_id"], sha256(raw))
    if state != "prepared":
        raise ValueError(
            f"Prepared plan is {state}; use explicit recovery for an interrupted apply"
        )
    if pending := ops._pending(ws):
        raise ValueError(
            f"Recover pending operations before applying a plan: {pending}"
        )
    ops._check_plan(ws, plan, plan["operation_id"], fresh=True)


def _summary(plan: dict[str, Any], raw: bytes) -> dict[str, Any]:
    changes = []
    for item in plan["writes"]:
        before = base64.b64decode(item["before_content"], validate=True)
        after = base64.b64decode(item["after"], validate=True)
        if sha256(before) != item["before"]:
            raise ValueError("Prepared before-image digest does not match")
        changes.append(
            {
                "path": item["path"],
                "before_sha256": sha256(before),
                "after_sha256": sha256(after),
            }
        )
    report = {
        "status": "prepared",
        "operation_id": plan["operation_id"],
        "plan_sha256": sha256(raw),
        "plan_file": f"{DIRECTORY}/{plan['operation_id']}.json",
        "workshop": plan["prepared_for"],
        "created": plan["created"],
        "action": plan["action"],
        "lora": plan["lora"],
        "asset": plan["asset"],
        "reason": plan["reason"],
        "inputs": plan["inputs"],
        "control_files": changes,
        "model_qualified": False,
    }
    if len(json.dumps(report, indent=2).encode("ascii")) + 1 > MAX_SUMMARY_BYTES:
        raise ValueError("Prepared plan summary exceeds the 6000-byte response limit")
    return report


def _prepare(ws: Workspace, plan: dict[str, Any]) -> dict[str, Any]:
    plan["prepared_for"] = str(ws.root)
    raw = json_bytes(plan)
    if len(raw) > ws.max_bytes:
        raise ValueError("Prepared plan exceeds its file limit")
    report = _summary(plan, raw)
    with ops._lock(ws):
        _ready(ws, plan, raw)
        directory = ws.resolve(DIRECTORY, write=True)
        count = 0
        for path in directory.glob("*.json"):
            if path.name.endswith(".cancelled.json"):
                continue
            previous, content = _load(ws, path.stem)
            if _state(ws, previous["operation_id"], sha256(content)) == "prepared":
                count += 1
        if count >= MAX_PENDING_PLANS:
            raise ValueError(
                "Prepared plan limit reached; explicitly cancel unused plans"
            )
        directory.mkdir(exist_ok=True)
        try:
            atomic_write(_path(ws, plan["operation_id"]), raw)
        except PublicationError as exc:
            raise PreparedPlanSaved(report, exc) from exc
    return report


def prepare_promotion(root: Path, candidate: str) -> dict[str, Any]:
    ws = ops.workspace(root)
    return _prepare(ws, ops._promotion_plan(ws, candidate))


def prepare_quarantine(root: Path, name: str, reason: str) -> dict[str, Any]:
    ws = ops.workspace(root)
    return _prepare(ws, ops._quarantine_plan(ws, name, reason))


def inspect_plan(root: Path, plan_id: str, *, details: bool = False) -> dict[str, Any]:
    """Review even stale/consumed plans without creating locks or changing files."""
    ws = ops.workspace(root)
    plan, raw = _load(ws, plan_id)
    report = _summary(plan, raw)
    report["status"] = _state(ws, plan_id, sha256(raw))
    try:
        _ready(ws, plan, raw)
        report["applicable"] = True
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report.update(applicable=False, application_error=str(exc))
    if details:
        report["changes"] = [
            {
                "path": item["path"],
                "before": base64.b64decode(item["before_content"]).decode("utf-8"),
                "after": base64.b64decode(item["after"]).decode("utf-8"),
            }
            for item in plan["writes"]
        ]
    return report


def apply_plan(
    root: Path, plan_id: str, expected_sha256: str, *, dry_run: bool = True
) -> dict[str, Any]:
    ws = ops.workspace(root)
    if dry_run:
        plan, raw = _load(ws, plan_id, expected_sha256)
        _ready(ws, plan, raw)
        return {**_summary(plan, raw), "status": "plan_preview"}
    with ops._lock(ws):
        plan, raw = _load(ws, plan_id, expected_sha256)
        _ready(ws, plan, raw)
        return ops._publish(ws, plan, raw)


def cancel_plan(root: Path, plan_id: str, expected_sha256: str) -> dict[str, Any]:
    ws = ops.workspace(root)
    with ops._lock(ws):
        _, raw = _load(ws, plan_id, expected_sha256)
        state = _state(ws, plan_id, sha256(raw))
        if state not in {"prepared", "cancelled"}:
            raise ValueError(
                f"Cannot cancel a plan in state {state}; inspect its journal"
            )
        record = {
            "status": "cancelled",
            "operation_id": plan_id,
            "plan_sha256": sha256(raw),
        }
        if state != "cancelled":
            atomic_write(_path(ws, plan_id, cancelled=True), json_bytes(record))
        return record


def check_recovery(
    ws: Workspace, plan_id: str, raw: bytes, plan: dict[str, Any]
) -> None:
    """Prepared operations recover only from the retained reviewed bytes."""
    if _path(ws, plan_id, cancelled=True).exists():
        raise ValueError("Conflicting cancellation marker and operation journal")
    path = _path(ws, plan_id)
    if "prepared_for" in plan or path.exists():
        _, prepared = _load(ws, plan_id)
        if prepared != raw:
            raise ValueError("Recovery journal differs from the reviewed prepared plan")
