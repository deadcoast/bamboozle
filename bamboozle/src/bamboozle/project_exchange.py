"""Authenticated neutral-project transport; translation stays with the source owner.

The bounded, uncompressed frame contains only public view files and opaque origin
digests. Classical transforms run before Tessera encryption and after authenticated
decryption. Neither inverse dictionaries nor recipes travel in the project frame.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any

from bamboozle.agent_files import check_public_name, source_path
from bamboozle.cipher_operations import _recipe
from bamboozle.classical import Pipeline
from bamboozle.project_views import (
    CONTRACT,
    GENERATED_FILES,
    MAX_FILES,
    digest,
    generated_contracts,
    load_view,
    operation_lock,
    relative_name,
    snapshot_view,
    unique_paths,
)
from bamboozle.service import OperationError
from bamboozle.tessera import seal, unseal
from bamboozle.utils import (
    MAX_FILE_BYTES,
    PublicationError,
    atomic_write,
    exclusive_lock,
    json_bytes,
    parse_json,
    read_bytes,
    sha256,
)
from bamboozle.workspace import PROTECTED, Workspace

MAGIC = b"BAMBOOZLE-PROJECT\x01"
STORAGE = Path(".local/bamboozle/exchanges")
HEADER_LIMIT = 512 * 1024
PUBLIC_CONTROLS = {CONTRACT, *GENERATED_FILES}


def _hashes(snapshot: dict[str, bytes]) -> dict[str, str]:
    return {name: sha256(raw) for name, raw in sorted(snapshot.items())}


def _name(name: str) -> str:
    name = relative_name(name)
    path = Path(name)
    check_public_name(path)
    if any(part.casefold() in PROTECTED for part in path.parts):
        raise ValueError("Exchange path enters protected repository state")
    if path.name.casefold().startswith(".env") or path.suffix.casefold() in {
        ".pem",
        ".p12",
        ".pfx",
    }:
        raise ValueError("Private credentials cannot enter a project exchange")
    return name


def _header(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "schema_version", "direction", "binding", "files"}
        or value["kind"] != "bamboozle.project-exchange"
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["direction"] not in ("outbound", "return")
    ):
        raise ValueError("Invalid project exchange header")
    binding = value["binding"]
    if (
        not isinstance(binding, dict)
        or set(binding) != {"view_id", "manifest_sha256", "controls"}
        or not isinstance(binding["view_id"], str)
        or re.fullmatch(r"[a-f0-9]{32}", binding["view_id"]) is None
        or not isinstance(binding["controls"], dict)
        or CONTRACT not in binding["controls"]
        or not set(binding["controls"]) <= PUBLIC_CONTROLS
    ):
        raise ValueError("Invalid project exchange origin binding")
    digest(binding["manifest_sha256"])
    for fingerprint in binding["controls"].values():
        digest(fingerprint)
    rows = value["files"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES + len(
        PUBLIC_CONTROLS
    ):
        raise ValueError("Project exchange file count exceeds its limit")
    total = 0
    names = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "bytes", "sha256"}
            or type(row["bytes"]) is not int
            or not 0 <= row["bytes"] <= MAX_FILE_BYTES
        ):
            raise ValueError("Invalid project exchange file entry")
        names.append(_name(row["path"]))
        digest(row["sha256"])
        total += row["bytes"]
    unique_paths(names)
    if names != sorted(names) or total > MAX_FILE_BYTES:
        raise ValueError("Exchange files must be ordered and fit the 16 MiB limit")
    hashes = {row["path"]: row["sha256"] for row in rows}
    if any(
        hashes.get(name) != expected for name, expected in binding["controls"].items()
    ):
        raise ValueError("Generated exchange controls were removed or changed")
    if (set(hashes) & PUBLIC_CONTROLS) != set(binding["controls"]):
        raise ValueError("Unexpected generated exchange control")
    return value


def _frame(
    binding: dict[str, Any], direction: str, snapshot: dict[str, bytes]
) -> bytes:
    header = _header(
        {
            "kind": "bamboozle.project-exchange",
            "schema_version": 1,
            "direction": direction,
            "binding": binding,
            "files": [
                {"path": name, "bytes": len(raw), "sha256": sha256(raw)}
                for name, raw in sorted(snapshot.items())
            ],
        }
    )
    encoded = json_bytes(header)
    if len(encoded) > HEADER_LIMIT:
        raise ValueError("Project exchange header exceeds its limit")
    size = 4 + len(encoded) + sum(len(raw) for raw in snapshot.values())
    if size > MAX_FILE_BYTES:
        raise ValueError("Project exchange frame exceeds 16 MiB")
    return (
        struct.pack(">I", len(encoded))
        + encoded
        + b"".join(raw for _, raw in sorted(snapshot.items()))
    )


def _unframe(raw: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    if len(raw) < 4 or len(raw) > MAX_FILE_BYTES:
        raise ValueError("Invalid project exchange frame length")
    size = struct.unpack(">I", raw[:4])[0]
    if not 0 < size <= HEADER_LIMIT or 4 + size > len(raw):
        raise ValueError("Invalid project exchange header length")
    header = _header(parse_json(raw[4 : 4 + size]))
    cursor = 4 + size
    snapshot = {}
    for row in header["files"]:
        data = raw[cursor : cursor + row["bytes"]]
        if len(data) != row["bytes"] or sha256(data) != row["sha256"]:
            raise ValueError("Project exchange file length or digest mismatch")
        snapshot[row["path"]] = data
        cursor += row["bytes"]
    if cursor != len(raw):
        raise ValueError("Project exchange contains trailing bytes")
    return header, snapshot


def _credentials(
    workspace: Workspace,
    key: str,
    recipe: str | None,
) -> tuple[bytes, Pipeline | None, dict[Path, str]]:
    path = workspace.resolve(key)
    if path.suffix.casefold() != ".key":
        raise ValueError("Project exchange keys require the private .key suffix")
    raw = read_bytes(path, 32)
    if len(raw) != 32:
        raise ValueError("Project exchange requires a 32-byte Tessera key")
    inputs = {path: sha256(raw)}
    pipeline = None
    if recipe is not None:
        recipe_path, encoded, pipeline = _recipe(workspace, recipe)
        inputs[recipe_path] = sha256(encoded)
    return raw, pipeline, inputs


def _recheck(workspace: Workspace, inputs: dict[Path, str]) -> None:
    if any(
        sha256(workspace.read(path)) != expected for path, expected in inputs.items()
    ):
        raise ValueError("Exchange input changed during the operation")


def read_exchange(
    workspace: Workspace,
    packet: str,
    *,
    key: str,
    recipient: str,
    recipe: str | None = None,
) -> tuple[dict[str, Any], dict[str, bytes], str]:
    """Authenticate before interpreting paths or reversing a selected classical recipe."""
    path = source_path(workspace, packet, private_paths=set())
    raw = read_bytes(path, workspace.max_bytes * 2)
    secret, pipeline, inputs = _credentials(workspace, key, recipe)
    opened = unseal(raw, secret, recipient)
    if len(opened) > workspace.max_bytes:
        raise ValueError("Project exchange payload exceeds the workspace byte limit")
    width = len(MAGIC) + 32
    fingerprint = bytes.fromhex(pipeline.fingerprint) if pipeline else bytes(32)
    if len(opened) < width or opened[:width] != MAGIC + fingerprint:
        raise ValueError("Wrong project exchange format or classical recipe")
    framed = opened[width:]
    if pipeline:
        framed = pipeline.transform(framed, reverse=True, limit=workspace.max_bytes)
    header, snapshot = _unframe(framed)
    _recheck(workspace, inputs)
    if read_bytes(path, workspace.max_bytes * 2) != raw:
        raise ValueError("Exchange packet changed during authentication")
    return header, snapshot, sha256(raw)


def _publish(
    workspace: Workspace,
    view: Workspace,
    binding: dict[str, Any],
    direction: str,
    output: str,
    *,
    key: str,
    recipient: str,
    recipe: str | None,
    inputs: dict[Path, str],
    snapshot: dict[str, bytes],
) -> dict[str, Any]:
    destination = source_path(workspace, output, private_paths=set(), write=True)
    if destination.is_relative_to(view.root):
        raise ValueError("Exchange packets must remain outside the neutral workspace")
    if destination.exists():
        raise FileExistsError(
            "Exchange output already exists; choose a new packet path"
        )
    raw = _frame(binding, direction, snapshot)
    secret, pipeline, credentials = _credentials(workspace, key, recipe)
    if any(path.is_relative_to(view.root) for path in credentials):
        raise ValueError("Keys and recipes must remain outside the neutral workspace")
    if pipeline:
        transformed = pipeline.transform(raw, limit=workspace.max_bytes)
        if (
            pipeline.transform(transformed, reverse=True, limit=workspace.max_bytes)
            != raw
        ):
            raise ValueError("Project exchange recipe failed its exact inverse check")
        raw = transformed
    fingerprint = bytes.fromhex(pipeline.fingerprint) if pipeline else bytes(32)
    if len(MAGIC) + 32 + len(raw) > workspace.max_bytes:
        raise ValueError("Project exchange payload exceeds the workspace byte limit")
    packet = seal(MAGIC + fingerprint + raw, secret, recipient)
    _recheck(workspace, {**inputs, **credentials})
    if _hashes(snapshot_view(view)) != _hashes(snapshot):
        raise ValueError("Neutral project changed while preparing exchange")
    report = {
        "schema_version": 1,
        "status": "saved",
        "direction": direction,
        "view_id": binding["view_id"],
        "recipient": recipient,
        "output": destination.relative_to(workspace.root).as_posix(),
        "output_bytes": len(packet),
        "exchange_sha256": sha256(packet),
        "files": len(snapshot),
        "authenticated": True,
        "pipeline_sha256": pipeline.fingerprint if pipeline else None,
        "output_saved": False,
    }
    try:
        atomic_write(destination, packet)
        report["output_saved"] = True
        _recheck(workspace, {**inputs, **credentials})
        if _hashes(snapshot_view(view)) != _hashes(snapshot):
            raise ValueError("Neutral project changed during exchange publication")
        if read_bytes(destination, workspace.max_bytes * 2) != packet:
            raise ValueError("Published exchange changed before completion")
    except (OSError, ValueError) as exc:
        report.update(
            status="error",
            error=str(exc),
            output_saved=report["output_saved"] or isinstance(exc, PublicationError),
        )
        raise OperationError(str(exc), report) from exc
    return report


def pack_view(
    workspace: Workspace,
    view_id: str,
    output: str,
    *,
    key: str,
    recipient: str,
    recipe: str | None = None,
) -> dict[str, Any]:
    """Send an unchanged exported project; original spelling context stays local."""
    with exclusive_lock(operation_lock(workspace)):
        directory, manifest, manifest_sha, _, view = load_view(workspace, view_id)
        if (directory / "import.json").exists():
            raise ValueError("Export a fresh view before another exchange cycle")
        snapshot = snapshot_view(view)
        controls = generated_contracts(manifest)
        expected = {
            name: entry["view_sha256"] for name, entry in manifest["files"].items()
        }
        if _hashes(snapshot) != {**expected, **controls}:
            raise ValueError("Outbound view changed since export; export a fresh view")
        return _publish(
            workspace,
            view,
            {"view_id": view_id, "manifest_sha256": manifest_sha, "controls": controls},
            "outbound",
            output,
            key=key,
            recipient=recipient,
            recipe=recipe,
            inputs={directory / "manifest.json": manifest_sha},
            snapshot=snapshot,
        )


def _directory(workspace: Workspace, exchange_id: str) -> Path:
    return workspace.resolve(STORAGE / digest(exchange_id), write=True)


def _journal(
    workspace: Workspace, exchange_id: str
) -> tuple[Path, bytes, dict[str, Any], Workspace]:
    directory = _directory(workspace, exchange_id)
    raw = workspace.read(directory / "import.json")
    value = parse_json(raw)
    if (
        not isinstance(value, dict)
        or set(value)
        != {"kind", "schema_version", "exchange_sha256", "output", "header"}
        or value["kind"] != "bamboozle.exchange-open"
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["exchange_sha256"] != exchange_id
    ):
        raise ValueError("Invalid retained exchange journal")
    _header(value["header"])
    if value["header"]["direction"] != "outbound":
        raise ValueError("Only outbound projects can create agent workspaces")
    destination = source_path(
        workspace, relative_name(value["output"]), private_paths=set(), write=True
    )
    workspace.resolve(destination / CONTRACT, write=True)
    return directory, raw, value, Workspace(destination, workspace.max_bytes)


def exchange_context(workspace: Workspace, exchange_id: str) -> dict[str, Any]:
    """Public receiver context only; no source mapping, key or recipe is returned."""
    directory, raw, journal, view = _journal(workspace, exchange_id)
    if parse_json(workspace.read(directory / "result.json")) != {
        "kind": "bamboozle.exchange-open-complete",
        "journal_sha256": sha256(raw),
    }:
        raise ValueError("Invalid completed exchange receipt")
    if any(
        sha256(view.read(name)) != expected
        for name, expected in journal["header"]["binding"]["controls"].items()
    ):
        raise ValueError("Generated exchange controls changed")
    return {
        "exchange_id": exchange_id,
        "workspace": str(view.root),
        "view_id": journal["header"]["binding"]["view_id"],
        "contract": str(view.root / CONTRACT),
        "mcp_config": {"mcpServers": {}},
        "instructions": "Develop and execute the complete application in this already neutral workspace using ordinary file tools. Preserve generated controls. Operator exchange-return packages all edited/new files; missing files require explicit deletion selection at the source owner. Do not attach the source encoder or operator MCP to this agent context.",
    }


def open_exchange(
    workspace: Workspace,
    packet: str,
    output: str,
    *,
    key: str,
    recipient: str,
    recipe: str | None = None,
    recover: bool = False,
) -> dict[str, Any]:
    """Materialize an authenticated outbound view with journaled, explicit recovery."""
    header, snapshot, exchange_id = read_exchange(
        workspace, packet, key=key, recipient=recipient, recipe=recipe
    )
    if header["direction"] != "outbound":
        raise ValueError("Use view-review --return-packet for a returned project")
    destination = source_path(
        workspace, relative_name(output), private_paths=set(), write=True
    )
    directory = _directory(workspace, exchange_id)
    journal_path, receipt_path = directory / "import.json", directory / "result.json"
    journal = json_bytes(
        {
            "kind": "bamboozle.exchange-open",
            "schema_version": 1,
            "exchange_sha256": exchange_id,
            "output": output,
            "header": header,
        }
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "exchange_id": exchange_id,
        "output": output,
        "journal_saved": False,
        "receipt_saved": False,
        "files_saved": [],
        "output_saved": False,
    }
    with exclusive_lock(operation_lock(workspace)):
        if receipt_path.exists():
            raise ValueError(
                "Exchange already opened; use its retained workspace/context"
            )
        if recover:
            if workspace.read(journal_path) != journal:
                raise ValueError(
                    "Recovery must use the exact retained packet and destination"
                )
        elif journal_path.exists() or destination.exists():
            raise FileExistsError(
                "Exchange destination/state must be new; use explicit recovery for partial state"
            )
        try:
            if not recover:
                directory.mkdir(parents=True, exist_ok=True)
                atomic_write(journal_path, journal)
            report["journal_saved"] = True
            destination.mkdir(parents=True, exist_ok=True)
            view = Workspace(
                workspace.resolve(destination, write=True), workspace.max_bytes
            )
            present = snapshot_view(view)
            if any(
                name not in snapshot or snapshot[name] != raw
                for name, raw in present.items()
            ):
                raise ValueError(
                    "Partial exchange workspace contains changed or unexpected files"
                )
            for name, raw in snapshot.items():
                path = view.resolve(name, write=True)
                if name not in present:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(view.resolve(name, write=True), raw)
                report["files_saved"].append(name)
            if _hashes(snapshot_view(view)) != _hashes(snapshot):
                raise ValueError("Exchange workspace changed before completion")
            if workspace.read(journal_path) != journal:
                raise ValueError("Exchange journal changed before completion")
            atomic_write(
                receipt_path,
                json_bytes(
                    {
                        "kind": "bamboozle.exchange-open-complete",
                        "journal_sha256": sha256(journal),
                    }
                ),
            )
            report.update(
                status="recovered" if recover else "opened", receipt_saved=True
            )
            report["output_saved"] = True
        except (OSError, ValueError, TypeError) as exc:
            report.update(
                status="error",
                error=str(exc),
                journal_saved=journal_path.is_file(),
                receipt_saved=receipt_path.is_file(),
            )
            try:
                retained = snapshot_view(Workspace(workspace.resolve(destination)))
                report["files_saved"] = [
                    name for name, raw in retained.items() if snapshot.get(name) == raw
                ]
                report["output_saved"] = bool(report["files_saved"])
            except (OSError, ValueError):
                report["observation_incomplete"] = True
            raise OperationError(str(exc), report) from exc
    return {**report, "agent_context": exchange_context(workspace, exchange_id)}


def pack_return(
    workspace: Workspace,
    exchange_id: str,
    output: str,
    *,
    key: str,
    recipient: str,
    recipe: str | None = None,
) -> dict[str, Any]:
    """Return the receiver's complete edited/new project, retaining its origin binding."""
    with exclusive_lock(operation_lock(workspace)):
        exchange_context(workspace, exchange_id)
        directory, raw, journal, view = _journal(workspace, exchange_id)
        receipt = workspace.read(directory / "result.json")
        return _publish(
            workspace,
            view,
            journal["header"]["binding"],
            "return",
            output,
            key=key,
            recipient=recipient,
            recipe=recipe,
            inputs={
                directory / "import.json": sha256(raw),
                directory / "result.json": sha256(receipt),
            },
            snapshot=snapshot_view(view),
        )


def returned_snapshot(
    workspace: Workspace,
    manifest: dict[str, Any],
    manifest_sha: str,
    packet: str,
    *,
    key: str,
    recipient: str,
    recipe: str | None = None,
) -> tuple[dict[str, bytes], str]:
    header, snapshot, fingerprint = read_exchange(
        workspace, packet, key=key, recipient=recipient, recipe=recipe
    )
    if header["direction"] != "return" or header["binding"] != {
        "view_id": manifest["view_id"],
        "manifest_sha256": manifest_sha,
        "controls": generated_contracts(manifest),
    }:
        raise ValueError(
            "Returned project belongs to a different view or retained context"
        )
    return snapshot, fingerprint
