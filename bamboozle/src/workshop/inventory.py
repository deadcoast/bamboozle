"""Bounded, read-only discovery of native prepared records, including damaged ones."""

from __future__ import annotations

import base64
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

MAX_DIRECTORY_ENTRIES = 10_000
MAX_PAGE_SIZE = 10
MAX_PAGE_BYTES = 6000
MAX_READ_BYTES = 64 * 1024**2
MAX_CURSOR_BYTES = 4096


class _BudgetExceeded(ValueError):
    pass


class _ChangingRecord(ValueError):
    pass


@dataclass(frozen=True)
class _Reader(Workspace):
    budget: list[int] = field(default_factory=lambda: [MAX_READ_BYTES])
    observed: dict[Path, tuple[int, int, int]] = field(default_factory=dict)

    def read(self, name: str | Path) -> bytes:
        path = self.resolve(name)
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("Inventory metadata must be a regular file")
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Inventory metadata must be a regular file")
            if before.st_size > self.max_bytes:
                raise ValueError("Inventory metadata exceeds the file size limit")
            if before.st_size > self.budget[0] or self.budget[0] == 0:
                raise _BudgetExceeded(
                    "Page metadata read budget exhausted; use a smaller page or inspect this ID"
                )
            raw = stream.read(min(self.max_bytes + 1, self.budget[0]))
            self.budget[0] -= len(raw)
            after = os.fstat(stream.fileno())
        identity = (before.st_ino, before.st_size, before.st_mtime_ns)
        if (
            identity != (after.st_ino, after.st_size, after.st_mtime_ns)
            or len(raw) != before.st_size
        ):
            raise _ChangingRecord("Metadata changed during inventory; inspect again")
        if path in self.observed and self.observed[path] != identity:
            raise _ChangingRecord("Metadata changed during inventory; inspect again")
        self.observed[path] = identity
        return raw

    def check_observed(self) -> None:
        for path, identity in self.observed.items():
            current = path.stat()
            if (current.st_ino, current.st_size, current.st_mtime_ns) != identity:
                raise _ChangingRecord(
                    "Metadata changed during inventory; inspect again"
                )


def _catalog(ws: Workspace) -> list[str]:
    directory = ws.resolve(plans.DIRECTORY)
    if not directory.exists():
        return []
    names: list[str] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if len(names) >= MAX_DIRECTORY_ENTRIES:
                raise ValueError(
                    "Inventory directory exceeds the 10000-entry scan limit; no records returned"
                )
            names.append(entry.name)
    return sorted(names)


def _encode_cursor(workshop: str, catalog: str, after: str) -> str:
    data = json.dumps(
        {"v": 1, "workshop": workshop, "catalog": catalog, "after": after},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(data.encode("ascii")).decode("ascii")


def _decode_cursor(cursor: str, workshop: str, catalog: str, names: list[str]) -> str:
    if not isinstance(cursor, str) or len(cursor) > MAX_CURSOR_BYTES:
        raise ValueError("Invalid inventory cursor")
    try:
        data = parse_json(base64.b64decode(cursor, altchars=b"-_", validate=True))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid inventory cursor") from exc
    if (
        not isinstance(data, dict)
        or set(data) != {"v", "workshop", "catalog", "after"}
        or type(data["v"]) is not int
        or data["v"] != 1
        or data["workshop"] != workshop
        or data["catalog"] != catalog
        or not isinstance(data["after"], str)
        or data["after"] not in names
    ):
        raise ValueError(
            "Inventory cursor does not match this workshop/catalog; restart listing"
        )
    return data["after"]


def _entry(ws: _Reader, name: str) -> dict[str, Any]:
    row: dict[str, Any] = {"file": f"{plans.DIRECTORY}/{name}"}
    if re.fullmatch(r"[0-9a-f]{32}\.json", name) is None:
        return {
            **row,
            "status": "corrupt",
            "error": "Unexpected record name or orphan cancellation marker",
        }
    operation_id = name[:-5]
    row["operation_id"] = operation_id
    ws.observed.clear()
    try:
        plan, raw = plans._load(ws, operation_id)
        asset = plan["asset"]
        if (
            any(
                not isinstance(asset.get(key), str) or not asset[key]
                for key in ("source", "destination", "sha256")
            )
            or re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]) is None
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in plan["inputs"].values()
            )
        ):
            raise ValueError("Invalid asset or input digest fields")
        summary = plans._summary(plan, raw)
        # Inventory fields must remain small even when a hand-edited record is damaged.
        if any(
            not isinstance(summary[key], str) or len(summary[key]) > 64
            for key in ("action", "lora", "created")
        ):
            raise ValueError("Invalid prepared plan summary fields")
        row.update(
            plan_sha256=summary["plan_sha256"],
            action=summary["action"],
            lora=summary["lora"],
            created=summary["created"],
        )
        row["status"] = plans._state(ws, operation_id, summary["plan_sha256"])
        if (
            row["status"] in {"applied", "recovery_required"}
            and plans._path(ws, operation_id, cancelled=True).exists()
        ):
            raise ValueError("Conflicting cancellation marker and operation journal")
        if row["status"] == "prepared":
            try:
                ops._check_plan(ws, plan, operation_id, fresh=True, verify_asset=False)
            except (_BudgetExceeded, _ChangingRecord):
                raise
            except (OSError, ValueError) as exc:
                row.update(status="stale", error=str(exc)[:160])
        ws.check_observed()
    except _BudgetExceeded:
        raise
    except _ChangingRecord as exc:
        row.update(status="changing", error=str(exc))
    except OSError as exc:
        row.update(status="unavailable", error=str(exc)[:160])
    except (ValueError, TypeError, KeyError) as exc:
        row.update(status="corrupt", error=str(exc)[:160])
    return row


def list_plans(
    root: Path, *, limit: int = 5, cursor: str | None = None
) -> dict[str, Any]:
    """Discover metadata states; asset digests and apply readiness require inspection."""
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("Inventory limit must be an integer from 1 to 10")
    ws = _Reader(root)
    ops._validate_workspace(ws)
    names = _catalog(ws)
    catalog = sha256(json.dumps(names, ensure_ascii=True).encode("ascii"))
    workshop = sha256(str(ws.root).encode("utf-8"))
    after = (
        _decode_cursor(cursor, workshop, catalog, names) if cursor is not None else ""
    )
    name_set = set(names)
    candidates = [
        name
        for name in names
        if name > after
        and not (
            re.fullmatch(r"[0-9a-f]{32}\.cancelled\.json", name)
            and name.replace(".cancelled.json", ".json") in name_set
        )
    ]
    rows: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "inventory",
        "items": rows,
        "next_cursor": None,
        "has_more": False,
        "catalog_sha256": catalog,
        "freshness": "metadata_only",
        "asset_hashes_checked": False,
        "apply_preflight": False,
        "metadata_bytes_read": 0,
    }
    for index, name in enumerate(candidates[:limit]):
        try:
            row = _entry(ws, name)
        except _BudgetExceeded as exc:
            if rows:
                break
            row = {
                "file": f"{plans.DIRECTORY}/{name}",
                "status": "unavailable",
                "error": str(exc),
            }
            if re.fullmatch(r"[0-9a-f]{32}\.json", name):
                row["operation_id"] = name[:-5]
        has_more = index + 1 < len(candidates)
        proposed = {
            **report,
            "items": [*rows, row],
            "has_more": has_more,
            "next_cursor": _encode_cursor(workshop, catalog, name)
            if has_more
            else None,
            "metadata_bytes_read": MAX_READ_BYTES - ws.budget[0],
        }
        bounded = {**proposed, "metadata_bytes_read": MAX_READ_BYTES}
        if len(json.dumps(bounded, indent=2).encode("ascii")) + 1 > MAX_PAGE_BYTES:
            if not rows:
                raise ValueError(
                    "Inventory record exceeds the response limit; inspect the directory"
                )
            break
        rows.append(row)
        report.update({key: value for key, value in proposed.items() if key != "items"})
    if _catalog(ws) != names:
        raise ValueError("Inventory catalog changed during listing; restart listing")
    report["metadata_bytes_read"] = MAX_READ_BYTES - ws.budget[0]
    return report
