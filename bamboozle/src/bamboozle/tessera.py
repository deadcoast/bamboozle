"""Authenticated, recipient-bound packets for local agent exchange."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bamboozle.utils import MAX_FILE_BYTES, atomic_write, json_bytes, parse_json
from bamboozle.workspace import Workspace

KIND = "vigiles.tessera"
ALGORITHM = "AES-256-GCM"


def _recipient(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(not (c.isascii() and (c.isalnum() or c in "._-")) for c in value)
    ):
        raise ValueError(
            "Recipient must be 1-128 ASCII letters, digits, dots, underscores or hyphens"
        )
    return value


def create_key(workspace: Workspace, name: str | Path) -> Path:
    path = workspace.resolve(name, write=True)
    atomic_write(path, AESGCM.generate_key(bit_length=256))
    return path


def _cipher(key: bytes) -> AESGCM:
    if len(key) != 32:
        raise ValueError("Tessera requires a 32-byte AES-256 key")
    return AESGCM(key)


def seal(data: bytes, key: bytes, recipient: str) -> bytes:
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Tessera payload exceeds the file limit")
    header = {
        "kind": KIND,
        "schema_version": 1,
        "algorithm": ALGORITHM,
        "recipient": _recipient(recipient),
    }
    nonce = os.urandom(12)
    ciphertext = _cipher(key).encrypt(nonce, data, json_bytes(header))
    return json_bytes(
        {
            **header,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
    )


def unseal(packet: bytes, key: bytes, recipient: str) -> bytes:
    if len(packet) > MAX_FILE_BYTES * 2:
        raise ValueError("Tessera packet exceeds the file limit")
    raw: Any = parse_json(packet)
    fields = {"kind", "schema_version", "algorithm", "recipient", "nonce", "ciphertext"}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise ValueError("Invalid Tessera packet schema")
    if (
        raw["kind"] != KIND
        or type(raw["schema_version"]) is not int
        or raw["schema_version"] != 1
        or raw["algorithm"] != ALGORITHM
    ):
        raise ValueError("Unsupported Tessera packet version or algorithm")
    if raw["recipient"] != _recipient(recipient):
        raise ValueError("Packet is addressed to a different recipient")
    if not isinstance(raw["nonce"], str) or not isinstance(raw["ciphertext"], str):
        raise TypeError("Tessera nonce and ciphertext must be base64 strings")
    nonce = base64.b64decode(raw["nonce"], validate=True)
    ciphertext = base64.b64decode(raw["ciphertext"], validate=True)
    if (
        len(nonce) != 12
        or len(ciphertext) < 16
        or len(ciphertext) > MAX_FILE_BYTES + 16
    ):
        raise ValueError("Invalid Tessera nonce or ciphertext length")
    header = {
        name: raw[name] for name in ("kind", "schema_version", "algorithm", "recipient")
    }
    try:
        return _cipher(key).decrypt(nonce, ciphertext, json_bytes(header))
    except InvalidTag as exc:
        raise ValueError("Tessera authentication failed") from exc
