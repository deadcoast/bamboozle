"""Bounded observations of retained recovery evidence and current file state.

This reader deliberately bypasses the completed-snapshot consumer boundary: its
purpose is to describe interrupted publication. It never supplies application
data to consumers, takes a writer lock, or changes workshop state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bamboozle.utils import parse_json, sha256
from bamboozle.workspace import Workspace
from workshop import operations as ops
from workshop import plans

MAX_METADATA_BYTES = 64 * 1024**2
MAX_DIRECTORY_ENTRIES = 10_000
MAX_RESPONSE_BYTES = 6000


class _LimitExceeded(ValueError):
    pass


class _Changing(ValueError):
    pass


def _identity(info: os.stat_result | None) -> tuple[int, ...] | None:
    if info is None:
        return None
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
    )


@dataclass(frozen=True)
class _Reader(Workspace):
    observed: dict[Path, tuple[int, ...] | None] = field(default_factory=dict)
    cached: dict[Path, bytes] = field(default_factory=dict)
    counts: list[int] = field(default_factory=lambda: [0, 0])

    def observe(self, name: str | Path) -> tuple[Path, os.stat_result | None]:
        path = self.resolve(name)
        try:
            info = path.stat()
        except FileNotFoundError:
            info = None
        identity = _identity(info)
        if path in self.observed and self.observed[path] != identity:
            raise _Changing("Observed file changed during diagnosis; inspect again")
        self.observed[path] = identity
        return path, info

    def read(self, name: str | Path) -> bytes:
        path, info = self.observe(name)
        if info is None:
            raise FileNotFoundError("Required file is missing")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Diagnosis requires a regular file")
        if path in self.cached:
            return self.cached[path]
        remaining = MAX_METADATA_BYTES - self.counts[0]
        if info.st_size > self.max_bytes or info.st_size > remaining or remaining == 0:
            raise _LimitExceeded(
                "Metadata read limit reached; no recovery readiness established"
            )
        with path.open("rb") as stream:
            if _identity(os.fstat(stream.fileno())) != _identity(info):
                raise _Changing("Metadata changed before reading; inspect again")
            raw = stream.read(min(self.max_bytes + 1, remaining))
            self.counts[0] += len(raw)
            if (
                _identity(os.fstat(stream.fileno())) != _identity(info)
                or len(raw) != info.st_size
            ):
                raise _Changing("Metadata changed while reading; inspect again")
        self.observe(path)
        self.cached[path] = raw
        return raw

    def asset_digest(self, name: str) -> str | None:
        path, info = self.observe(name)
        if info is None:
            return None
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Asset is not a regular file")
        if info.st_size > ops.MAX_ASSET_BYTES:
            raise _LimitExceeded("Asset exceeds the 8 GiB inspection limit")
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as stream:
            if _identity(os.fstat(stream.fileno())) != _identity(info):
                raise _Changing("Asset changed before hashing; inspect again")
            while total < info.st_size:
                chunk = stream.read(min(1024**2, info.st_size - total))
                if not chunk:
                    break
                total += len(chunk)
                self.counts[1] += len(chunk)
                digest.update(chunk)
            if (
                _identity(os.fstat(stream.fileno())) != _identity(info)
                or total != info.st_size
            ):
                raise _Changing("Asset changed while hashing; inspect again")
        self.observe(path)
        return digest.hexdigest()

    def recheck(self) -> None:
        for path in list(self.observed):
            self.observe(path)


def _failure(exc: Exception, *, missing: str = "unavailable") -> dict[str, str]:
    state = (
        "changing"
        if isinstance(exc, _Changing)
        else missing
        if isinstance(exc, FileNotFoundError)
        else "unavailable"
    )
    return {"state": state, "error": str(exc)[:160]}


def _catalog(ws: _Reader) -> tuple[str, ...]:
    path = ws.resolve("scrape/transactions")
    names: list[str] = []
    with os.scandir(path) as entries:
        for entry in entries:
            if len(names) >= MAX_DIRECTORY_ENTRIES:
                raise _LimitExceeded(
                    "Transaction directory exceeds the 10000-entry inspection limit"
                )
            names.append(entry.name)
    return tuple(sorted(names))


def _transactions(
    ws: _Reader, names: tuple[str, ...], operation_id: str
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    total = 0
    state = "clear"
    for name in names:
        if not name.endswith(".json") or name.endswith(".done.json"):
            continue
        current = name[:-5]
        if current == operation_id:
            continue
        try:
            completed = ops._completed(ws, current)
            # _completed checks absence with Path.exists; observe it as well so
            # publication of a completion record invalidates this observation.
            ws.observe(f"scrape/transactions/{current}.done.json")
            if completed:
                continue
            issue = {"operation_id": current, "state": "pending"}
        except (_Changing, _LimitExceeded, OSError):
            raise
        except (ValueError, TypeError, KeyError) as exc:
            issue = {"file": name, "state": "corrupt", "error": str(exc)[:120]}
        state = "conflict"
        total += 1
        if len(issues) < 5:
            issues.append(issue)
    return {
        "state": state,
        "blocking_count": total,
        "blockers": issues,
        "omitted_blockers": max(0, total - len(issues)),
    }


def _current(
    ws: _Reader, plan: dict, decoded: list[tuple[str, bytes, bytes]], report: dict
) -> None:
    for name, expected in plan["inputs"].items():
        row = {"path": name, "expected_sha256": expected}
        try:
            actual = sha256(ws.read(name))
            row.update(
                state="matching" if actual == expected else "conflict",
                observed_sha256=actual,
            )
        except (OSError, ValueError) as exc:
            row.update(_failure(exc, missing="conflict"))
        report["inputs"].append(row)
    for name, before, after in decoded:
        before_hash, after_hash = sha256(before), sha256(after)
        row = {"path": name, "before_sha256": before_hash, "after_sha256": after_hash}
        try:
            actual = sha256(ws.read(name))
            state = (
                "matching"
                if actual == after_hash
                else "pending"
                if actual == before_hash
                else "conflict"
            )
            row.update(state=state, observed_sha256=actual)
        except (OSError, ValueError) as exc:
            row.update(_failure(exc, missing="conflict"))
        report["controls"].append(row)
    asset = plan["asset"]
    if asset is None:
        return
    for role in ("source", "destination"):
        name = asset[role]
        row = {"role": role, "path": name, "expected_sha256": asset["sha256"]}
        try:
            directory = (
                ("candidates" if plan["action"] == "promote" else "prod")
                if role == "source"
                else ("prod" if plan["action"] == "promote" else "quarantine")
            )
            ops._inside(ws, name, directory)
            actual_asset = ws.asset_digest(name)
            state = (
                "absent"
                if actual_asset is None
                else "matching"
                if actual_asset == asset["sha256"]
                else "conflict"
            )
            # Empty assets are rejected by the actual recovery hasher too.
            if actual_asset == sha256(b""):
                state = "conflict"
            row.update(state=state, observed_sha256=actual_asset)
        except (OSError, ValueError) as exc:
            row.update(_failure(exc))
        report["assets"].append(row)
    source, destination = report["assets"]
    if source["state"] == "absent":
        source["state"] = (
            "removed"
            if plan["action"] == "quarantine" and destination["state"] == "matching"
            else "conflict"
        )
    if destination["state"] == "absent":
        destination["state"] = "pending"


def diagnose(root: Path, operation_id: str) -> dict[str, Any]:
    """Return bounded, observational recovery guidance; never authorize a write."""
    if (
        not isinstance(operation_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None
    ):
        raise ValueError("Invalid operation ID")
    ws = _Reader(root)
    report: dict[str, Any] = {
        "schema_version": 1,
        "operation_id": operation_id,
        "status": "unavailable",
        "read_only": True,
        "recovery_ready": False,
        "inspection_complete": False,
        "current_state_checked": False,
        "writer_lock_checked": False,
        "records": {},
        "inputs": [],
        "controls": [],
        "assets": [],
    }
    records = report["records"]

    def record(role: str, path: str) -> bytes | None:
        try:
            raw = ws.read(path)
            records[role] = {"state": "read", "sha256": sha256(raw)}
            return raw
        except (OSError, ValueError) as exc:
            records[role] = _failure(exc, missing="absent")
            return None

    try:
        ops._validate_workspace(ws)
        records["workshop"] = {"state": "valid"}
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        records["workshop"] = _failure(exc)
        return _finalize(ws, report)

    prefix = f"scrape/transactions/{operation_id}"
    raw = record("journal", prefix + ".json")
    prepared = record("prepared_plan", f"{plans.DIRECTORY}/{operation_id}.json")
    done = record("completion", prefix + ".done.json")
    cancelled = record(
        "cancellation", f"{plans.DIRECTORY}/{operation_id}.cancelled.json"
    )
    plan = None
    decoded = []
    if raw is not None:
        try:
            plan = parse_json(raw)
            if not isinstance(plan, dict):
                raise TypeError("Operation journal must be an object")
            decoded = ops._validate_plan(ws, plan, operation_id)
            records["journal"]["state"] = "valid"
            report["action"] = plan["action"]
        except (ValueError, TypeError, KeyError) as exc:
            records["journal"].update(state="corrupt", error=str(exc)[:160])
            plan = None
    if prepared is not None:
        try:
            plans._load(ws, operation_id)
            if raw is not None and raw != prepared:
                raise ValueError(
                    "Recovery journal differs from the reviewed prepared plan"
                )
            records["prepared_plan"]["state"] = "valid"
        except (OSError, ValueError, TypeError, KeyError) as exc:
            records["prepared_plan"].update(state="corrupt", error=str(exc)[:160])
    elif (
        plan is not None
        and "prepared_for" in plan
        and records["prepared_plan"]["state"] == "absent"
    ):
        records["prepared_plan"].update(
            state="corrupt",
            error="Required reviewed plan is missing; preserve the journal",
        )
    completed = False
    if done is not None:
        try:
            completed = ops._completed(ws, operation_id)
            records["completion"]["state"] = "valid"
        except (OSError, ValueError, TypeError, KeyError) as exc:
            records["completion"].update(state="corrupt", error=str(exc)[:160])
    if cancelled is not None:
        try:
            if prepared is None or parse_json(cancelled) != {
                "status": "cancelled",
                "operation_id": operation_id,
                "plan_sha256": sha256(prepared),
            }:
                raise ValueError("Cancellation does not match a retained prepared plan")
            if raw is not None or done is not None:
                raise ValueError(
                    "Conflicting cancellation marker and operation journal"
                )
            records["cancellation"]["state"] = "valid"
        except (ValueError, TypeError) as exc:
            records["cancellation"].update(state="corrupt", error=str(exc)[:160])
    if raw is None:
        report["status"] = (
            "cancelled"
            if cancelled is not None
            else "not_started"
            if prepared is not None
            else "not_found"
        )
    elif completed:
        # A valid completed operation may have been superseded. Its before/after
        # images are historical, not expected current state.
        report["status"] = "already_completed"
    elif plan is not None:
        report["status"] = "recoverable"
        names = None
        try:
            names = _catalog(ws)
            report["transactions"] = _transactions(ws, names, operation_id)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            report["transactions"] = _failure(exc)
        _current(ws, plan, decoded, report)
        report["current_state_checked"] = True
        if names is not None:
            try:
                if _catalog(ws) != names:
                    raise _Changing(
                        "Transaction membership changed during diagnosis; inspect again"
                    )
            except (OSError, ValueError) as exc:
                report["transactions"] = _failure(exc)
    return _finalize(ws, report)


def _finalize(ws: _Reader, report: dict[str, Any]) -> dict[str, Any]:
    try:
        ws.recheck()
    except (OSError, ValueError) as exc:
        report["observation"] = _failure(exc)
    states = {
        row["state"]
        for row in [
            *report["records"].values(),
            *report["inputs"],
            *report["controls"],
            *report["assets"],
            report.get("transactions", {"state": "clear"}),
            report.get("observation", {"state": "clear"}),
        ]
    }
    for state in ("changing", "unavailable", "corrupt", "conflict"):
        if state in states:
            report["status"] = state
            break
    report["inspection_complete"] = not bool(states & {"changing", "unavailable"})
    report["recovery_ready"] = report["status"] == "recoverable"
    report["metadata_bytes_read"], report["asset_bytes_hashed"] = ws.counts
    report["next_action"] = {
        "recoverable": "Review these observations, then explicitly run --recover with this operation ID. Recovery rechecks state under the writer lock; this report reserves nothing.",
        "already_completed": "No recovery needed. Completion binds the retained journal; current files may belong to later operations.",
        "not_started": "No journal exists. Review the retained plan with --show-plan before any digest-bound application.",
        "cancelled": "The unused plan was cancelled. Preserve its records; do not recover or apply it.",
        "not_found": "Check the selected workshop and operation ID; no operation evidence was found.",
        "corrupt": "Preserve the damaged records. Compare them with trusted retained evidence; explicit recovery will not repair or replace them.",
        "conflict": "Inspect every conflicting path and blocking operation against the retained journal. Preserve changed files; resolve their provenance before explicit recovery.",
        "changing": "Publication or files changed during inspection. Wait for the writer to stop, then explicitly diagnose again.",
        "unavailable": "Resolve the reported access or inspection limit before diagnosing again. Recovery readiness has not been established.",
    }[report["status"]]
    size = len(json.dumps(report, indent=2).encode("ascii")) + 1
    if size > MAX_RESPONSE_BYTES:
        return {
            "schema_version": 1,
            "operation_id": report["operation_id"],
            "status": "unavailable",
            "read_only": True,
            "recovery_ready": False,
            "inspection_complete": False,
            "required_response_bytes": size,
            "response_limit_bytes": MAX_RESPONSE_BYTES,
            "metadata_bytes_read": ws.counts[0],
            "asset_bytes_hashed": ws.counts[1],
            "next_action": "Diagnostic details exceed the response limit. Inspect the selected journal and referenced paths locally; no recovery readiness is established.",
        }
    return report
