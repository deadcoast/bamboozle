"""Bounded, read-only snapshots fenced by native operation journals.

Native writers publish a journal before any control changes and retain it after
completion. A reader accepts bytes only when the same completed journal set
surrounds the read. No locks, caches, or directories are created by inspection.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from bamboozle.utils import parse_json, read_bytes


def is_native(scrape: Path) -> bool:
    marker = scrape.parent / "_vigiles_workshop.json"
    return marker.exists() or marker.is_symlink()


def capture(scrape: Path, paths: list[Path]) -> dict[Path, bytes]:
    """Return one completed native revision, or fail without publishing data.

    External workshops retain bounded reads but cannot claim journal consistency.
    This protocol covers cooperating native writers, not hostile file replacement
    or manual edits that bypass the journal. There is no automatic retry.
    """
    if not is_native(scrape):
        return {path: read_bytes(path) for path in paths}

    from workshop import operations as ops

    ws = ops.workspace(scrape.parent)

    def fence() -> tuple[str, ...]:
        directory = ws.resolve("scrape/transactions")
        if not directory.is_dir():
            raise FileNotFoundError("Native workshop transaction directory is missing")
        journals = tuple(
            sorted(
                path.stem
                for path in directory.glob("*.json")
                if not path.name.endswith(".done.json")
            )
        )
        pending = [name for name in journals if not ops._completed(ws, name)]
        if pending:
            raise ValueError(
                f"Native workshop has pending operations; recover first: {pending}"
            )
        return journals

    before = fence()
    # Include all controls even for a one-file query; status must agree with the
    # journal set and every caller must reject incomplete native initialization.
    selected = list(
        dict.fromkeys([ws.root / name for name in sorted(ops.CONTROLS)] + paths)
    )
    content = {path: ws.read(path.absolute()) for path in selected}
    after = fence()
    if before != after:
        raise ValueError("Native workshop changed during snapshot; retry the read")
    status = parse_json(content[ws.root / ops.STATUS])
    if not isinstance(status, dict):
        raise TypeError("Native workshop status must be an object")
    operation = status.get("operation_id")
    if operation is None:
        if status.get("status") != "initialized" or after:
            raise ValueError("Native workshop status does not match its journals")
    elif operation not in after or status.get("status") != "publication_recorded":
        raise ValueError("Native workshop status does not match its completed journal")
    return {path: content[path] for path in paths}


def load_json(path: Path) -> dict[str, Any]:
    if not is_native(path.parent) and not path.exists():
        return {}
    data = parse_json(capture(path.parent, [path])[path])
    if not isinstance(data, dict):
        raise TypeError("Workshop metadata must be a JSON object")
    return data


def ledger_tail(path: Path, n: int) -> list[dict[str, Any]]:
    if not 1 <= n <= 1000:
        raise ValueError("Ledger row count must be between 1 and 1000")
    if not is_native(path.parent) and not path.exists():
        return []
    raw = capture(path.parent, [path])[path].decode("utf-8")
    return list(csv.DictReader(io.StringIO(raw, newline="")))[-n:]
