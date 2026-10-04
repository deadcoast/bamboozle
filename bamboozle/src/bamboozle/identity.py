"""Operator-owned symmetric peer bindings and durable, operation-bound admission.

This is local key custody, not independent identity verification. The workspace
and its replay records must be trusted and must not be rolled back. All writers
use the same process lock, held across admitted publication and key retirement.
"""

from __future__ import annotations

import copy
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from bamboozle.tessera import (
    _recipient,
    inspect_managed,
    managed_header,
    seal_managed,
    unseal_managed,
)
from bamboozle.utils import (
    atomic_write,
    exclusive_lock,
    json_bytes,
    parse_json,
    read_bytes,
    sha256,
)
from bamboozle.workspace import Workspace

ROOT = Path(".vigiles/identity")
REGISTRY = ROOT / "registry.json"
REGISTRY_LIMIT = 1024 * 1024


def _digest(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("Invalid identity digest")
    return value


@contextmanager
def locked(workspace: Workspace) -> Iterator[None]:
    directory = workspace.resolve(ROOT, write=True)
    directory.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(workspace.resolve(ROOT / "operation.lock", write=True)):
        yield


def _registry(workspace: Workspace) -> tuple[dict[str, Any], str]:
    raw = read_bytes(workspace.resolve(REGISTRY), REGISTRY_LIMIT)
    value = parse_json(raw)
    if (
        not isinstance(value, dict)
        or set(value)
        != {"kind", "schema_version", "identity", "revision", "peers", "events"}
        or value["kind"] != "bamboozle.identity"
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["revision"]) is not int
        or value["revision"] < 0
        or not isinstance(value["peers"], dict)
        or not isinstance(value["events"], list)
        or len(value["events"]) != value["revision"]
    ):
        raise ValueError("Invalid local recipient registry")
    _recipient(value["identity"])
    all_keys: set[str] = set()
    for peer, record in value["peers"].items():
        _recipient(peer)
        if (
            peer == value["identity"]
            or not isinstance(record, dict)
            or set(record) != {"active", "keys"}
            or not isinstance(record["keys"], dict)
            or not record["keys"]
        ):
            raise ValueError("Invalid recipient binding")
        active = []
        for key_id, state in record["keys"].items():
            _digest(key_id)
            if key_id in all_keys or state not in ("active", "retired", "revoked"):
                raise ValueError("Duplicate key binding or invalid lifecycle state")
            all_keys.add(key_id)
            if state == "active":
                active.append(key_id)
        if active != ([] if record["active"] is None else [record["active"]]):
            raise ValueError("Recipient active key disagrees with its lifecycle")
    for index, event in enumerate(value["events"], 1):
        if (
            not isinstance(event, dict)
            or set(event) != {"revision", "action", "peer", "key_id", "at"}
            or type(event["revision"]) is not int
            or event["revision"] != index
            or event["action"] not in ("register", "rotate", "revoke")
            or event["peer"] not in value["peers"]
            or type(event["at"]) is not int
            or event["key_id"] not in value["peers"][event["peer"]]["keys"]
        ):
            raise ValueError("Invalid recipient lifecycle history")
    return value, sha256(raw)


def initialize(workspace: Workspace, identity: str) -> dict[str, Any]:
    _recipient(identity)
    with locked(workspace):
        value = {
            "kind": "bamboozle.identity",
            "schema_version": 1,
            "identity": identity,
            "revision": 0,
            "peers": {},
            "events": [],
        }
        atomic_write(workspace.resolve(REGISTRY, write=True), json_bytes(value))
        return status(workspace)


def status(workspace: Workspace) -> dict[str, Any]:
    registry, fingerprint = _registry(workspace)
    return {
        **registry,
        "registry_sha256": fingerprint,
        "trust": "Explicit local shared-key bindings; no independent sender identity verification",
    }


def change_peer(
    workspace: Workspace,
    peer: str,
    *,
    action: str,
    key: str | None = None,
    key_id: str | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Register, rotate, or irreversibly revoke a binding; never disclose secrets."""
    _recipient(peer)
    if action not in ("register", "rotate", "revoke"):
        raise ValueError("Unknown recipient lifecycle action")
    with locked(workspace):
        original, fingerprint = _registry(workspace)
        if action != "register" and expected_sha256 is None:
            raise ValueError(
                "Rotation and revocation require the current registry SHA-256"
            )
        if expected_sha256 is not None and _digest(expected_sha256) != fingerprint:
            raise ValueError(
                "Recipient registry changed; inspect identity-status again"
            )
        registry = copy.deepcopy(original)
        peers = registry["peers"]
        if peer == registry["identity"]:
            raise ValueError("A recipient must be distinct from the local identity")
        if (action == "register") == (peer in peers):
            raise ValueError(
                "Register requires a new peer; rotation/revocation requires an existing peer"
            )
        if action == "revoke":
            if key is not None or key_id not in peers[peer]["keys"]:
                raise ValueError(
                    "Revocation requires a registered key_id and no key file"
                )
            if peers[peer]["keys"][key_id] == "revoked":
                raise ValueError("Key is already revoked")
            peers[peer]["keys"][key_id] = "revoked"
            if peers[peer]["active"] == key_id:
                peers[peer]["active"] = None
        else:
            if key is None or key_id is not None:
                raise ValueError("Registration/rotation requires a private .key file")
            source = workspace.resolve(key)
            if source.suffix.casefold() != ".key":
                raise ValueError("Recipient keys require the private .key suffix")
            secret = read_bytes(source, 32)
            if len(secret) != 32:
                raise ValueError("Recipient keys must contain exactly 32 bytes")
            key_id = sha256(secret)
            if any(key_id in record["keys"] for record in peers.values()):
                raise ValueError(
                    "Key is already bound; use a fresh key for each peer and rotation"
                )
            if action == "register":
                peers[peer] = {"active": None, "keys": {}}
            record = peers[peer]
            if record["active"] is not None:
                record["keys"][record["active"]] = "retired"
            record["active"] = key_id
            record["keys"][key_id] = "active"
            stored = workspace.resolve(ROOT / "keys" / f"{key_id}.key", write=True)
            stored.parent.mkdir(parents=True, exist_ok=True)
            if stored.exists():
                if read_bytes(stored, 32) != secret:
                    raise ValueError("Retained key fingerprint mismatch")
            else:
                atomic_write(stored, secret)
            if read_bytes(source, 32) != secret:
                raise ValueError("Provisioning key changed during registration")
        registry["revision"] += 1
        registry["events"].append(
            {
                "revision": registry["revision"],
                "action": action,
                "peer": peer,
                "key_id": key_id,
                "at": int(time.time()),
            }
        )
        encoded = json_bytes(registry)
        if len(encoded) > REGISTRY_LIMIT:
            raise ValueError("Recipient registry exceeds its 1 MiB limit")
        if _registry(workspace)[1] != fingerprint:
            raise ValueError("Recipient registry changed during update")
        atomic_write(workspace.resolve(REGISTRY, write=True), encoded, overwrite=True)
        return {"status": action, "peer": peer, "key_id": key_id, **status(workspace)}


def _key(
    workspace: Workspace,
    registry: dict[str, Any],
    peer: str,
    key_id: str,
    *,
    sending: bool = False,
) -> bytes:
    _recipient(peer)
    _digest(key_id)
    record = registry["peers"].get(peer)
    state = record["keys"].get(key_id) if record else None
    if state not in (("active",) if sending else ("active", "retired")):
        raise ValueError("Unknown, revoked or inactive recipient key")
    secret = read_bytes(workspace.resolve(ROOT / "keys" / f"{key_id}.key"), 32)
    if len(secret) != 32 or sha256(secret) != key_id:
        raise ValueError(
            "Recipient key bytes disagree with their registered fingerprint"
        )
    return secret


@contextmanager
def sending(
    workspace: Workspace, peer: str, data: bytes, purpose: str, ttl: int = 86400
) -> Iterator[bytes]:
    with locked(workspace):
        registry, fingerprint = _registry(workspace)
        record = registry["peers"].get(_recipient(peer))
        if record is None or record["active"] is None:
            raise ValueError("Recipient has no active sending key")
        key_id = record["active"]
        packet = seal_managed(
            data,
            _key(workspace, registry, peer, key_id, sending=True),
            sender=registry["identity"],
            recipient=peer,
            key_id=key_id,
            purpose=purpose,
            ttl=ttl,
        )
        yield packet
        if _registry(workspace)[1] != fingerprint:
            raise ValueError("Recipient registry changed during publication")
        _key(workspace, registry, peer, key_id, sending=True)


def validate_certificate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "header",
        "packet_sha256",
        "payload_sha256",
    }:
        raise ValueError("Invalid retained delivery certificate")
    managed_header(value["header"])
    _digest(value["packet_sha256"])
    _digest(value["payload_sha256"])
    return value


def delivery_id(certificate: dict[str, Any]) -> str:
    header = validate_certificate(certificate)["header"]
    return sha256(
        json_bytes(
            {name: header[name] for name in ("sender", "recipient", "message_id")}
        )
    )


def _intent_path(workspace: Workspace, identifier: str) -> Path:
    return workspace.resolve(
        ROOT / "deliveries" / f"{_digest(identifier)}.json", write=True
    )


def _fresh(header: dict[str, Any]) -> None:
    now = int(time.time())
    if header["issued_at"] > now + 60 or header["expires_at"] <= now:
        raise ValueError("Tessera delivery is expired or issued in the future")


def _authorized(workspace: Workspace, certificate: dict[str, Any]) -> None:
    header = validate_certificate(certificate)["header"]
    registry, _ = _registry(workspace)
    if header["recipient"] != registry["identity"]:
        raise ValueError("Packet is addressed to a different local identity")
    _receiving_key(workspace, registry, header)


def _receiving_key(
    workspace: Workspace, registry: dict[str, Any], header: dict[str, Any]
) -> bytes:
    secret = _key(workspace, registry, header["sender"], header["key_id"])
    if registry["peers"][header["sender"]]["keys"][header["key_id"]] == "retired":
        selected = False
        for event in registry["events"]:
            if event["peer"] != header["sender"] or event["action"] == "revoke":
                continue
            if selected:
                if header["issued_at"] > event["at"]:
                    raise ValueError(
                        "Packet was issued after its receiving key retired"
                    )
                break
            selected = event["key_id"] == header["key_id"]
        else:
            raise ValueError("Retired key has no lifecycle transition")
    return secret


def delivery_status(workspace: Workspace, identifier: str) -> dict[str, Any]:
    path = _intent_path(workspace, identifier)
    intent = parse_json(workspace.read(path))
    if (
        not isinstance(intent, dict)
        or set(intent) != {"kind", "certificate", "receipt", "receipt_sha256"}
        or intent["kind"] != "bamboozle.delivery"
        or delivery_id(intent["certificate"]) != identifier
    ):
        raise ValueError("Invalid retained delivery admission")
    receipt = workspace.resolve(intent["receipt"])
    if not receipt.is_relative_to(workspace.resolve(".local/bamboozle")):
        raise ValueError("Delivery completion must use a private operation receipt")
    _digest(intent["receipt_sha256"])
    complete = receipt.exists()
    if complete and sha256(workspace.read(receipt)) != intent["receipt_sha256"]:
        raise ValueError("Delivery completion receipt changed")
    return {
        "delivery_id": identifier,
        "status": "completed" if complete else "pending",
        **intent,
    }


def authenticate(
    workspace: Workspace,
    packet: bytes,
    peer: str,
    purpose: str,
    *,
    recover: bool = False,
) -> tuple[bytes, dict[str, Any]]:
    """Read-only preview. Admission happens separately immediately before writes."""
    header, _, _ = inspect_managed(packet)
    registry, _ = _registry(workspace)
    if (
        header["sender"] != _recipient(peer)
        or header["recipient"] != registry["identity"]
        or header["purpose"] != purpose
    ):
        raise ValueError("Managed Tessera sender, recipient or purpose mismatch")
    secret = _receiving_key(workspace, registry, header)
    payload, header = unseal_managed(packet, secret)
    certificate = {
        "header": header,
        "packet_sha256": sha256(packet),
        "payload_sha256": sha256(payload),
    }
    identifier = delivery_id(certificate)
    path = _intent_path(workspace, identifier)
    if path.exists():
        prior = delivery_status(workspace, identifier)
        if (
            not recover
            or prior["status"] == "completed"
            or prior["certificate"] != certificate
        ):
            raise ValueError(
                "Delivery replay refused; pending delivery requires exact explicit recovery"
            )
    else:
        _fresh(header)
    return payload, certificate


@contextmanager
def admitting(
    workspace: Workspace,
    certificate: dict[str, Any],
    receipt: Path,
    receipt_bytes: bytes,
    *,
    recover: bool = False,
) -> Iterator[str]:
    """Reserve a delivery for one exact completion receipt while holding custody.

    Call after retaining the operation journal and before any authoritative write.
    A crash before reservation can resume from that trusted private journal. A
    crash after the domain receipt needs no second commit: that receipt completes
    admission. Neither expired messages nor receipt loss erases replay records.
    """
    with locked(workspace):
        _authorized(workspace, certificate)
        identifier = delivery_id(certificate)
        path = _intent_path(workspace, identifier)
        relative = (
            workspace.resolve(receipt, write=True)
            .relative_to(workspace.root)
            .as_posix()
        )
        if not relative.startswith(".local/bamboozle/"):
            raise ValueError("Delivery requires a private operation receipt")
        intent = {
            "kind": "bamboozle.delivery",
            "certificate": certificate,
            "receipt": relative,
            "receipt_sha256": sha256(receipt_bytes),
        }
        if path.exists():
            previous = delivery_status(workspace, identifier)
            if (
                not recover
                or previous["status"] == "completed"
                or {name: previous[name] for name in intent} != intent
            ):
                raise ValueError("Delivery replay or changed recovery intent refused")
        else:
            _fresh(certificate["header"])
            if workspace.resolve(receipt).exists():
                raise ValueError(
                    "Completion receipt exists without its delivery admission"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, json_bytes(intent))
        yield identifier
        _authorized(workspace, certificate)


def select_mode(key: str | None, recipient: str | None, peer: str | None) -> None:
    if peer is not None:
        _recipient(peer)
        if key is not None or recipient is not None:
            raise ValueError("Use --peer alone or the explicit --key/--recipient pair")
    elif key is None or recipient is None:
        raise ValueError("Transport requires --peer or both --key and --recipient")
