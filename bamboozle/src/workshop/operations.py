"""Native, journaled file operations for an explicitly initialized workshop."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import re
import sys
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from bamboozle.utils import atomic_write, json_bytes, parse_json, sha256, utc_now
from bamboozle.workspace import Workspace
from workshop.pipeline import _manifest, _matrix

MANIFEST = "scrape/_lora_manifest.json"
MATRIX = "scrape/_compatibility_matrix.json"
LEDGER = "scrape/_lora_ledger.csv"
STATUS = "scrape/_pipeline_status.json"
CONTROLS = {MANIFEST, MATRIX, LEDGER, STATUS}
FIELDS = ["operation_id", "created", "action", "lora", "sha256", "reason"]
MAX_ASSET_BYTES = 8 * 1024**3


class PendingOperation(OSError):
    def __init__(self, operation_id: str, cause: Exception):
        super().__init__(f"Operation {operation_id} needs recovery: {cause}")
        self.operation_id = operation_id


def workspace(root: Path) -> Workspace:
    ws = Workspace(root)
    marker = parse_json(ws.read("_vigiles_workshop.json"))
    if (
        marker != {"kind": "vigiles.workshop", "schema_version": 1}
        or type(marker.get("schema_version")) is not int
    ):
        raise ValueError("Unsupported native workshop marker")
    return ws


def _json(ws: Workspace, name: str) -> dict[str, Any]:
    data = parse_json(ws.read(name))
    if not isinstance(data, dict):
        raise TypeError("Workshop metadata must be a JSON object")
    return data


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ASSET_BYTES:
                raise ValueError("Asset exceeds the 8 GiB limit")
            digest.update(chunk)
    if total == 0:
        raise ValueError("Asset must be nonempty")
    return digest.hexdigest()


def _inside(ws: Workspace, name: str, directory: str) -> Path:
    path = ws.resolve(name)
    if not path.is_relative_to(ws.root / directory):
        raise ValueError(f"Path must stay inside {directory}")
    return path


def _relative(ws: Workspace, path: Path) -> str:
    return path.relative_to(ws.root).as_posix()


@contextmanager
def _lock(ws: Workspace) -> Iterator[None]:
    path = ws.resolve("scrape/.vigiles.lock", write=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OSError("Another native workshop operation is active") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _pending(ws: Workspace) -> list[str]:
    directory = ws.resolve("scrape/transactions")
    if directory.is_symlink() or directory.is_junction():
        raise ValueError("Transaction directory cannot be a link")
    return sorted(
        path.stem
        for path in directory.glob("*.json")
        if not path.name.endswith(".done.json") and not _completed(ws, path.stem)
    )


def _completed(ws: Workspace, operation_id: str) -> bool:
    if re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        raise ValueError("Unexpected operation journal name")
    path = ws.resolve(f"scrape/transactions/{operation_id}.done.json")
    if not path.exists():
        return False
    record = _json(ws, _relative(ws, path))
    digest = sha256(ws.read(f"scrape/transactions/{operation_id}.json"))
    if (
        record.get("status") != "saved"
        or record.get("operation_id") != operation_id
        or record.get("journal_sha256") != digest
    ):
        raise ValueError("Completion record does not match its operation journal")
    return True


def _asset_copy(source: Path, destination: Path, digest: str) -> None:
    fd, name = tempfile.mkstemp(dir=destination.parent, suffix=".partial")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as incoming:
            copied = hashlib.sha256()
            total = 0
            while chunk := incoming.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ASSET_BYTES:
                    raise ValueError("Asset exceeds the 8 GiB limit")
                copied.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if copied.hexdigest() != digest:
            raise ValueError("Asset changed while copying")
        if os.name == "nt":
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _plan(
    ws: Workspace,
    observed: dict[str, bytes],
    action: str,
    name: str,
    replacements: dict[str, bytes],
    *,
    asset: dict[str, Any] | None = None,
    inputs: dict[str, str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    operation = uuid.uuid4().hex
    created = utc_now()
    previous = observed[LEDGER].decode("utf-8")
    if next(csv.reader(io.StringIO(previous)), None) != FIELDS:
        raise ValueError("Unsupported workshop ledger columns")
    output = io.StringIO(newline="")
    output.write(previous)
    if previous and not previous.endswith("\n"):
        output.write("\n")
    csv.writer(output).writerow(
        [operation, created, action, name, asset["sha256"] if asset else "", reason]
    )
    replacements[LEDGER] = output.getvalue().encode("utf-8")
    replacements[STATUS] = json_bytes(
        {
            "operation_id": operation,
            "created": created,
            "action": action,
            "status": "publication_recorded",
        }
    )
    writes = []
    for path, content in replacements.items():
        if len(content) > ws.max_bytes:
            raise ValueError("Workshop metadata exceeds its file limit")
        writes.append(
            {
                "path": path,
                "before": sha256(observed[path]),
                "before_content": base64.b64encode(observed[path]).decode("ascii"),
                "after": base64.b64encode(content).decode("ascii"),
            }
        )
    plan = {
        "kind": "vigiles.workshop.operation",
        "schema_version": 1,
        "operation_id": operation,
        "action": action,
        "lora": name,
        "created": created,
        "asset": asset,
        "inputs": {
            **{
                name: sha256(raw)
                for name, raw in observed.items()
                if name not in replacements
            },
            **(inputs or {}),
        },
        "writes": writes,
    }
    if len(json_bytes(plan)) > ws.max_bytes:
        raise ValueError("Operation journal exceeds its file limit")
    return plan


def _check_plan(ws: Workspace, plan: dict[str, Any], operation_id: str) -> None:
    if (
        plan.get("kind") != "vigiles.workshop.operation"
        or type(plan.get("schema_version")) is not int
        or plan["schema_version"] != 1
        or plan.get("operation_id") != operation_id
        or plan.get("action") not in {"promote", "quarantine", "audit"}
        or not isinstance(plan.get("inputs"), dict)
        or not isinstance(plan.get("writes"), list)
        or not all(isinstance(item, dict) for item in plan["writes"])
        or (
            plan.get("asset") is not None
            if plan.get("action") == "audit"
            else not isinstance(plan.get("asset"), dict)
        )
    ):
        raise ValueError("Unsupported workshop operation journal")
    for name, digest in plan["inputs"].items():
        if sha256(ws.read(name)) != digest:
            raise ValueError(f"Operation input changed: {name}")
    paths = [item["path"] for item in plan["writes"]]
    if len(paths) != len(set(paths)) or not set(paths) <= CONTROLS:
        raise ValueError("Journal contains unsupported control-file destinations")
    for item in plan["writes"]:
        before = base64.b64decode(item["before_content"], validate=True)
        if sha256(before) != item["before"]:
            raise ValueError("Journal before-image digest does not match")
        after = base64.b64decode(item["after"], validate=True)
        if len(after) > ws.max_bytes:
            raise ValueError("Journal metadata exceeds its file limit")
        if sha256(ws.read(item["path"])) not in {item["before"], sha256(after)}:
            raise ValueError(f"Control file changed: {item['path']}")
    asset = plan["asset"]
    if asset:
        source = _inside(
            ws, asset["source"], "candidates" if plan["action"] == "promote" else "prod"
        )
        destination = _inside(
            ws,
            asset["destination"],
            "prod" if plan["action"] == "promote" else "quarantine",
        )
        if destination.exists() and _digest(destination) != asset["sha256"]:
            raise FileExistsError("Destination contains different asset bytes")
        if source.exists():
            if _digest(source) != asset["sha256"]:
                raise ValueError("Operation asset changed")
        elif plan["action"] != "quarantine" or not destination.exists():
            raise FileNotFoundError("Operation asset is missing")


def _finish(ws: Workspace, plan: dict[str, Any]) -> dict[str, Any]:
    operation = plan["operation_id"]
    try:
        _check_plan(ws, plan, operation)
        asset = plan["asset"]
        if asset:
            destination = ws.resolve(asset["destination"], write=True)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                _asset_copy(ws.resolve(asset["source"]), destination, asset["sha256"])
        for item in plan["writes"]:
            after = base64.b64decode(item["after"], validate=True)
            if ws.read(item["path"]) != after:
                atomic_write(
                    ws.resolve(item["path"], write=True), after, overwrite=True
                )
        if asset and plan["action"] == "quarantine":
            source = ws.resolve(asset["source"], write=True)
            if source.exists():
                if _digest(source) != asset["sha256"]:
                    raise ValueError(
                        "Production asset changed before quarantine removal"
                    )
                if _digest(ws.resolve(asset["destination"])) != asset["sha256"]:
                    raise ValueError(
                        "Quarantine copy changed before production removal"
                    )
                source.unlink()
        result = {
            "schema_version": 1,
            "status": "saved",
            "operation_id": operation,
            "action": plan["action"],
            "lora": plan["lora"],
            "model_qualified": False,
            "journal_sha256": sha256(ws.read(f"scrape/transactions/{operation}.json")),
        }
        atomic_write(
            ws.resolve(f"scrape/transactions/{operation}.done.json", write=True),
            json_bytes(result),
        )
        return result
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PendingOperation(operation, exc) from exc


def _execute(ws: Workspace, plan: dict[str, Any], apply: bool) -> dict[str, Any]:
    report = {
        "schema_version": 1,
        "status": "preview",
        "action": plan["action"],
        "lora": plan["lora"],
        "operation_id": plan["operation_id"],
        "asset": plan["asset"],
        "control_files": [item["path"] for item in plan["writes"]],
        "model_qualified": False,
    }
    if not apply:
        _check_plan(ws, plan, plan["operation_id"])
        return report
    with _lock(ws):
        if pending := _pending(ws):
            raise ValueError(
                f"Recover pending operations before another write: {pending}"
            )
        _check_plan(ws, plan, plan["operation_id"])
        journal = ws.resolve(
            f"scrape/transactions/{plan['operation_id']}.json", write=True
        )
        try:
            atomic_write(journal, json_bytes(plan))
        except OSError as exc:
            if journal.exists():
                raise PendingOperation(plan["operation_id"], exc) from exc
            raise
        return _finish(ws, plan)


def promote(root: Path, candidate: str, *, dry_run: bool = True) -> dict[str, Any]:
    ws = workspace(root)
    observed = {name: ws.read(name) for name in CONTROLS}
    path = _inside(ws, candidate, "candidates")
    raw = ws.read(path)
    data = parse_json(raw)
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("Candidate requires schema_version 1")
    name = data.get("id", "")
    if (
        not isinstance(name, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) is None
    ):
        raise ValueError("Candidate ID must be a simple 1-64 character identifier")
    _manifest({"loras": {name: data}})
    if not isinstance(data.get("base_model"), str) or not data["base_model"].strip():
        raise ValueError("Candidate requires a base_model")
    artifact = _inside(ws, data["artifact"], "candidates")
    if artifact.suffix.lower() not in {".safetensors", ".bin"}:
        raise ValueError("Candidate artifact must have .safetensors or .bin extension")
    digest = _digest(artifact)
    if data.get("sha256") != digest:
        raise ValueError("Candidate artifact digest does not match")
    validation_path = _inside(ws, data["validation"], "candidates")
    validation_bytes = ws.read(validation_path)
    validation = parse_json(validation_bytes)
    if (
        not isinstance(validation, dict)
        or validation.get("passed") is not True
        or validation.get("artifact_sha256") != digest
        or validation.get("kind") not in {"fixture", "external_evaluation"}
    ):
        raise ValueError(
            "Candidate needs passing validation tied to its artifact digest"
        )
    manifest, matrix = parse_json(observed[MANIFEST]), parse_json(observed[MATRIX])
    _manifest(manifest)
    _matrix(matrix, set(manifest["loras"]))
    if len(manifest["loras"]) >= 1000:
        raise ValueError("Native workshop supports at most 1000 active LoRAs")
    if any(existing.casefold() == name.casefold() for existing in manifest["loras"]):
        raise FileExistsError("LoRA ID already exists")
    destination = f"prod/model-{name}{artifact.suffix.lower()}"
    if ws.resolve(destination).exists():
        raise FileExistsError("Production destination already exists")
    entry = {
        field: data[field]
        for field in ("base_model", "tags", "categories", "triggers", "trained_words")
        if field in data
    }
    entry.update(
        path=destination,
        sha256=digest,
        validation_kind=validation["kind"],
        validation_sha256=sha256(validation_bytes),
    )
    manifest["loras"][name] = entry
    plan = _plan(
        ws,
        observed,
        "promote",
        name,
        {MANIFEST: json_bytes(manifest)},
        asset={
            "source": _relative(ws, artifact),
            "destination": destination,
            "sha256": digest,
        },
        inputs={
            _relative(ws, path): sha256(raw),
            _relative(ws, validation_path): sha256(validation_bytes),
        },
    )
    return _execute(ws, plan, not dry_run)


def quarantine(
    root: Path, name: str, reason: str, *, dry_run: bool = True
) -> dict[str, Any]:
    if not reason.strip() or len(reason) > 2000 or "\0" in reason:
        raise ValueError(
            "Quarantine requires a nonempty reason of at most 2000 characters"
        )
    ws = workspace(root)
    observed = {path: ws.read(path) for path in CONTROLS}
    manifest, matrix = parse_json(observed[MANIFEST]), parse_json(observed[MATRIX])
    _manifest(manifest)
    _matrix(matrix, None)
    if name not in manifest["loras"]:
        raise ValueError("LoRA is absent from the active manifest")
    entry = manifest["loras"][name]
    source = _inside(ws, entry["path"], "prod")
    digest = _digest(source)
    if digest != entry["sha256"]:
        raise ValueError("Production asset digest does not match")
    del manifest["loras"][name]
    matrix["pairs"] = {
        pair: value
        for pair, value in matrix["pairs"].items()
        if name not in pair.split("|")
    }
    destination = f"quarantine/{uuid.uuid4().hex}/{source.name}"
    plan = _plan(
        ws,
        observed,
        "quarantine",
        name,
        {MANIFEST: json_bytes(manifest), MATRIX: json_bytes(matrix)},
        asset={
            "source": _relative(ws, source),
            "destination": destination,
            "sha256": digest,
        },
        reason=reason,
    )
    return _execute(ws, plan, not dry_run)


def recover(root: Path, operation_id: str, *, dry_run: bool = True) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        raise ValueError("Invalid operation ID")
    ws = workspace(root)
    plan = _json(ws, f"scrape/transactions/{operation_id}.json")
    if _completed(ws, operation_id):
        return {"status": "already_completed", "operation_id": operation_id}
    _check_plan(ws, plan, operation_id)
    if dry_run:
        return {"status": "recovery_preview", "operation_id": operation_id}
    with _lock(ws):
        if _completed(ws, operation_id):
            return {"status": "already_completed", "operation_id": operation_id}
        if _pending(ws) != [operation_id]:
            raise ValueError(
                "Unexpected additional pending operation; inspect journals"
            )
        return _finish(ws, plan)


def pipeline_status(root: Path) -> dict[str, Any]:
    ws = workspace(root)
    status = _json(ws, STATUS)
    pending = _pending(ws)
    status["pending_operations"] = pending
    if pending:
        status["status"] = "recovery_required"
    elif re.fullmatch(r"[0-9a-f]{32}", status.get("operation_id", "")):
        status["status"] = (
            "saved" if _completed(ws, status["operation_id"]) else "recovery_required"
        )
    return status
