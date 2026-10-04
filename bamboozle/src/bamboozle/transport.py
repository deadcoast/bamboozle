"""Managed file delivery using the same custody and admission as project exchange."""

from __future__ import annotations

from typing import Any

from bamboozle import identity, structure
from bamboozle.agent_files import source_path
from bamboozle.project_views import operation_lock
from bamboozle.service import OperationError
from bamboozle.utils import (
    atomic_write,
    exclusive_lock,
    json_bytes,
    parse_json,
    read_bytes,
    sha256,
)
from bamboozle.workspace import Workspace


def transfer_file(
    workspace: Workspace,
    source: str,
    output: str,
    *,
    peer: str,
    opening: bool,
    recover: bool = False,
    ttl: int = 86400,
) -> dict[str, Any]:
    if recover and not opening:
        raise ValueError("Only incoming file deliveries support recovery")
    source_file = source_path(workspace, source, private_paths=set())
    destination = source_path(workspace, output, private_paths=set(), write=True)
    if source_file == destination:
        raise ValueError("Input and output must use distinct paths")
    raw = read_bytes(source_file, workspace.max_bytes * (2 if opening else 1))
    report: dict[str, Any] = {
        "status": "opened" if opening else "sealed",
        "peer": peer,
        "output": destination.relative_to(workspace.root).as_posix(),
        "output_saved": False,
        "receipt_saved": False,
    }
    attempted: bytes | None = None

    def publish(content: bytes, *, retained: bool = False) -> None:
        nonlocal attempted
        guard = structure.prepare_guard(workspace, {report["output"]: content})
        guard.recheck()
        if read_bytes(source_file, workspace.max_bytes * (2 if opening else 1)) != raw:
            raise ValueError("Transport input changed before publication")
        if destination.exists():
            if not retained or workspace.read(destination) != content:
                raise FileExistsError(
                    "Delivery output already exists or differs from retained content"
                )
        else:
            attempted = content
            atomic_write(destination, content)
        report.update(output_saved=True, output_bytes=len(content))
        guard.recheck()
        if read_bytes(destination, workspace.max_bytes * 2) != content:
            raise ValueError("Transport output changed before completion")

    with exclusive_lock(operation_lock(workspace)):
        try:
            if not opening:
                with identity.sending(workspace, peer, raw, "file", ttl) as packet:
                    publish(packet)
                report["packet_sha256"] = sha256(packet)
                return report
            content, certificate = identity.authenticate(
                workspace, raw, peer, "file", recover=recover
            )
            if len(content) > workspace.max_bytes:
                raise ValueError("Delivery payload exceeds the workspace byte limit")
            identifier = identity.delivery_id(certificate)
            report["delivery_id"] = identifier
            directory = workspace.resolve(
                f".local/bamboozle/deliveries/{identifier}", write=True
            )
            journal_path, receipt_path = (
                directory / "import.json",
                directory / "result.json",
            )
            guard = structure.prepare_guard(workspace, {report["output"]: content})
            intent = {
                "kind": "bamboozle.file-delivery",
                "certificate": certificate,
                "output": report["output"],
                "structure": guard.certificate,
            }
            if recover:
                saved = parse_json(workspace.read(journal_path))
                guard.pin(saved.get("structure") if isinstance(saved, dict) else None)
                if saved != intent:
                    raise ValueError(
                        "Recovery requires the original packet, destination and structural profile"
                    )
            elif journal_path.exists() or destination.exists():
                raise ValueError("Existing file delivery requires explicit recovery")
            guard.recheck()
            encoded = json_bytes(intent)
            receipt = json_bytes(
                {
                    "kind": "bamboozle.file-delivery-complete",
                    "journal_sha256": sha256(encoded),
                }
            )
            if not recover:
                directory.mkdir(parents=True, exist_ok=True)
                atomic_write(journal_path, encoded)
            report["journal_saved"] = True
            with identity.admitting(
                workspace, certificate, receipt_path, receipt, recover=recover
            ):
                guard.recheck()
                publish(content, retained=recover)
                guard.recheck()
                if workspace.read(journal_path) != encoded:
                    raise ValueError("File delivery journal changed before completion")
                atomic_write(receipt_path, receipt)
                report["receipt_saved"] = True
            report["status"] = "recovered" if recover else "opened"
            return report
        except (OSError, ValueError, TypeError) as exc:
            report.update(status="error", error=str(exc))
            try:
                report["output_exists"] = destination.exists()
                if attempted is not None and destination.exists():
                    report["output_saved"] = (
                        read_bytes(destination, workspace.max_bytes * 2) == attempted
                    )
                if opening and "delivery_id" in report:
                    report["delivery"] = identity.delivery_status(
                        workspace, report["delivery_id"]
                    )
            except (OSError, ValueError):
                report["observation_incomplete"] = True
            raise OperationError(str(exc), report) from exc
