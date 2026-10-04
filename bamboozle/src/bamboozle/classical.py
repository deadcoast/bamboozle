"""Versioned, lossless byte transforms derived from the supplied C cipher codex.

These classical algorithms provide obfuscation, not authenticated confidentiality.
They never replace semantic vocabulary translation, Tessera or BAMb5.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from math import gcd
from types import MappingProxyType
from typing import Any

from bamboozle.utils import MAX_FILE_BYTES, json_bytes, sha256

CHECKPOINT = "c53f0bfded51667437fc837cd33ede37745ae9c5"
MAX_STEPS = 8
MAX_KEY = 128
Transform = Callable[[bytes, Mapping[str, Any], bool], bytes]


def _integer(value: Any) -> int:
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise ValueError("Cipher integer parameters require signed 64-bit integers")
    return value


def _substitute(data: bytes, params: Mapping[str, Any], reverse: bool) -> bytes:
    a, b = params["a"], params["b"]
    if reverse:
        a, b = pow(a, -1, 26), (-pow(a, -1, 26) * b) % 26
    table = bytearray(range(256))
    for base in (65, 97):
        for index in range(26):
            table[base + index] = base + (a * index + b) % 26
    return data.translate(bytes(table))


def _keyed(data: bytes, params: Mapping[str, Any], reverse: bool) -> bytes:
    key = params["key"]
    digits, reciprocal = params["digits"], params["reciprocal"]
    shifts = [ord(char) - (48 if digits else 97) for char in key]
    out = bytearray(data)
    position = 0
    for index, value in enumerate(data):
        base = 65 if 65 <= value <= 90 else 97 if 97 <= value <= 122 else None
        if base is not None:
            shift = shifts[position % len(shifts)]
            symbol = value - base
            out[index] = (
                base
                + (
                    (shift - symbol)
                    if reciprocal
                    else (symbol - shift if reverse else symbol + shift)
                )
                % 26
            )
            position += 1
    return bytes(out)


def _columns(data: bytes, width: int, order: Iterable[int], reverse: bool) -> bytes:
    out = bytearray(len(data))
    offset = 0
    for column in order:
        count = (len(data) - column + width - 1) // width if column < len(data) else 0
        if reverse:
            out[column::width] = data[offset : offset + count]
        else:
            out[offset : offset + count] = data[column::width]
        offset += count
    return bytes(out)


def _scytale(data: bytes, params: Mapping[str, Any], reverse: bool) -> bytes:
    width = params["width"]
    if width >= len(data):
        return data
    return _columns(data, width, range(width), reverse)


def _columnar(data: bytes, params: Mapping[str, Any], reverse: bool) -> bytes:
    key = params["key"]
    order = sorted(range(len(key)), key=lambda index: (key[index], index))
    return _columns(data, len(key), order, reverse)


def _rail_positions(length: int, rails: int) -> Iterator[int]:
    cycle = 2 * (rails - 1)
    for rail in range(rails):
        for position in range(rail, length, cycle):
            yield position
            diagonal = position + cycle - 2 * rail
            if rail not in (0, rails - 1) and diagonal < length:
                yield diagonal


def _rail(data: bytes, params: Mapping[str, Any], reverse: bool) -> bytes:
    rails = params["rails"]
    if rails == 1 or rails >= len(data):
        return data
    out = bytearray(len(data))
    for index, position in enumerate(_rail_positions(len(data), rails)):
        if reverse:
            out[position] = data[index]
        else:
            out[index] = data[position]
    return bytes(out)


def _polybius(data: bytes, params: Mapping[str, Any], reverse: bool) -> bytes:
    del params
    alphabet = b"abcdefghiklmnopqrstuvwxyz"
    if reverse:
        if len(data) % 3:
            raise ValueError("Polybius frame must contain complete three-byte tokens")
        out = bytearray(len(data) // 3)
        for index in range(0, len(data), 3):
            tag, first, second = data[index : index + 3]
            if tag in (ord("l"), ord("u")) and 49 <= first <= 53 and 49 <= second <= 53:
                value = alphabet[(first - 49) * 5 + second - 49]
                out[index // 3] = value - 32 if tag == ord("u") else value
            elif (
                tag == ord("x")
                and first in b"0123456789abcdef"
                and second in b"0123456789abcdef"
            ):
                value = int(data[index + 1 : index + 3], 16)
                if value in alphabet or value in alphabet.upper():
                    raise ValueError(
                        "Noncanonical Polybius literal; use a coordinate token"
                    )
                out[index // 3] = value
            else:
                raise ValueError(f"Invalid Polybius token at byte {index}")
        return bytes(out)
    out = bytearray(len(data) * 3)
    for index, value in enumerate(data):
        lower = value + 32 if 65 <= value <= 90 else value
        position = alphabet.find(bytes((lower,)))
        if position >= 0:
            token = bytes(
                (
                    ord("u") if value != lower else ord("l"),
                    49 + position // 5,
                    49 + position % 5,
                )
            )
        else:
            token = f"x{value:02x}".encode("ascii")
        out[index * 3 : index * 3 + 3] = token
    return bytes(out)


@dataclass(frozen=True)
class Cipher:
    id: str
    name: str
    variant: str
    parameter_kind: str
    method: Transform = field(repr=False)
    reference: str
    compatibility: str

    def parameters(self, values: Any) -> Mapping[str, Any]:
        if not isinstance(values, dict):
            raise TypeError("Cipher parameters must be an object")
        required = {
            "none": set(),
            "shift": {"shift"},
            "affine": {"a", "b"},
            "width": {"width"},
            "rails": {"rails"},
            "letters": {"key"},
            "digits": {"key"},
            "keyword": {"key"},
        }[self.parameter_kind]
        if set(values) != required:
            raise ValueError(
                f"{self.id} requires exactly these parameters: {sorted(required)}"
            )
        result = dict(values)
        if self.parameter_kind == "shift":
            result = {"a": 1, "b": _integer(result["shift"]) % 26}
        elif self.parameter_kind == "affine":
            result = {name: _integer(result[name]) % 26 for name in ("a", "b")}
            if gcd(result["a"], 26) != 1:
                raise ValueError(
                    "Affine multiplier a must be coprime to 26 for an inverse"
                )
        elif self.id in ("rot13", "atbash"):
            result = {"a": 1, "b": 13} if self.id == "rot13" else {"a": 25, "b": 25}
        elif self.parameter_kind in ("rails", "width"):
            key = self.parameter_kind
            if _integer(result[key]) < 1:
                raise ValueError(f"{key} must be positive")
        elif self.parameter_kind in ("letters", "digits", "keyword"):
            key = result["key"]
            if not isinstance(key, str) or not 1 <= len(key) <= MAX_KEY:
                raise ValueError("Cipher keys require 1 to 128 characters")
            permitted = {
                "letters": lambda char: "a" <= char.lower() <= "z" and char.isascii(),
                "digits": lambda char: "0" <= char <= "9",
                "keyword": lambda char: 33 <= ord(char) <= 126,
            }[self.parameter_kind]
            if not all(permitted(char) for char in key):
                raise ValueError(
                    f"{self.parameter_kind} key contains an unsupported character"
                )
            if self.parameter_kind != "keyword":
                result = {
                    "key": key.lower(),
                    "digits": self.parameter_kind == "digits",
                    "reciprocal": self.id == "beaufort",
                }
        return MappingProxyType(result)

    def document(self) -> dict[str, Any]:
        integer = {"type": "integer", "minimum": -(2**63), "maximum": 2**63 - 1}
        properties: dict[str, Any] = {}
        if self.parameter_kind == "shift":
            properties = {"shift": integer}
        elif self.parameter_kind == "affine":
            properties = {"a": {**integer, "description": "Must be coprime to 26"}, "b": integer}
        elif self.parameter_kind in ("width", "rails"):
            properties = {self.parameter_kind: {**integer, "minimum": 1}}
        elif self.parameter_kind in ("letters", "digits", "keyword"):
            properties = {"key": {"type": "string", "minLength": 1, "maxLength": MAX_KEY,
                                  "pattern": {"letters": "^[A-Za-z]+$", "digits": "^[0-9]+$", "keyword": "^[!-~]+$"}[self.parameter_kind]}}
        return {
            "id": self.id,
            "name": self.name,
            "variant": self.variant,
            "parameters": self.parameter_kind,
            "parameter_schema": {"type": "object", "properties": properties, "required": sorted(properties), "additionalProperties": False},
            "reversible": True,
            "input": "bytes",
            "authenticated": False,
            "confidentiality": False,
            "reference": self.reference,
            "reference_checkpoint": CHECKPOINT,
            "compatibility": self.compatibility,
        }


_CIPHERS = (
    Cipher(
        "caesar",
        "Caesar",
        "ascii-v1",
        "shift",
        _substitute,
        "caesar.c",
        "C-compatible for defined integer inputs; modular extremes are defined",
    ),
    Cipher(
        "rot13",
        "ROT13",
        "ascii-v1",
        "none",
        _substitute,
        "rot13.c",
        "C-compatible on NUL-free input",
    ),
    Cipher(
        "atbash",
        "Atbash",
        "ascii-v1",
        "none",
        _substitute,
        "atbash.c",
        "C-compatible on NUL-free input",
    ),
    Cipher(
        "affine",
        "Affine",
        "ascii-v1",
        "affine",
        _substitute,
        "affine.c",
        "C-compatible for normalized valid multipliers; negative units are supported",
    ),
    Cipher(
        "scytale",
        "Scytale",
        "ragged-v1",
        "width",
        _scytale,
        "scytale.c",
        "No padding; differs from C when its final row is incomplete",
    ),
    Cipher(
        "polybius-square",
        "Polybius Square",
        "framed5-v1",
        "none",
        _polybius,
        "polybius_square.c",
        "Explicit l/u coordinates and x byte escapes; not the lossy C text format",
    ),
    Cipher(
        "vigenere",
        "Vigenere",
        "ascii-v1",
        "letters",
        _keyed,
        "vigenere.c",
        "C-compatible for alphabet-only keys; punctuation does not advance key",
    ),
    Cipher(
        "beaufort",
        "Beaufort",
        "ascii-v1",
        "letters",
        _keyed,
        "beaufort.c",
        "C-compatible for alphabet-only keys; reciprocal transform",
    ),
    Cipher(
        "gronsfeld",
        "Gronsfeld",
        "ascii-v1",
        "digits",
        _keyed,
        "gronsfeld.c",
        "C-compatible for digit-only keys; punctuation does not advance key",
    ),
    Cipher(
        "rail-fence",
        "Rail Fence",
        "zigzag-v1",
        "rails",
        _rail,
        "rail_fence.c",
        "C-compatible for positive rails without C arithmetic overflow",
    ),
    Cipher(
        "columnar-transposition",
        "Columnar Transposition",
        "ragged-v1",
        "keyword",
        _columnar,
        "columnar_transposition.c",
        "Stable duplicate-key columns; no padding; differs for incomplete rows",
    ),
)
REGISTRY: Mapping[str, Cipher] = MappingProxyType(
    {cipher.id: cipher for cipher in _CIPHERS}
)


@dataclass(frozen=True)
class Step:
    cipher: Cipher
    params: Mapping[str, Any] = field(repr=False)
    declared: Mapping[str, Any] = field(repr=False)


class Pipeline:
    """Validated immutable recipe; reversal applies inverses in reverse step order."""

    def __init__(self, document: Any):
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "steps",
        }:
            raise ValueError("Cipher recipe requires schema_version and steps only")
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != 1
        ):
            raise ValueError("Unsupported cipher recipe schema version")
        rows = document["steps"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_STEPS:
            raise ValueError("Cipher recipe requires 1 to 8 explicit steps")
        steps = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"cipher", "variant", "params"}:
                raise ValueError("Each cipher step requires cipher, variant and params")
            if not isinstance(row["cipher"], str) or row["cipher"] not in REGISTRY:
                raise ValueError("Unknown classical cipher; inspect cipher-catalog")
            cipher = REGISTRY[row["cipher"]]
            if row["variant"] != cipher.variant:
                raise ValueError(
                    f"{cipher.id} requires explicit variant {cipher.variant}"
                )
            steps.append(
                Step(
                    cipher,
                    cipher.parameters(row["params"]),
                    MappingProxyType(dict(row["params"])),
                )
            )
        self._steps = tuple(steps)

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "steps": [
                {
                    "cipher": step.cipher.id,
                    "variant": step.cipher.variant,
                    "params": dict(step.declared),
                }
                for step in self._steps
            ],
        }

    @property
    def fingerprint(self) -> str:
        return sha256(json_bytes(self.document()))

    def transform(
        self, data: bytes, *, reverse: bool = False, limit: int = MAX_FILE_BYTES
    ) -> bytes:
        if not isinstance(data, bytes) or type(reverse) is not bool:
            raise TypeError(
                "Cipher transform requires bytes and an explicit boolean direction"
            )
        if type(limit) is not int or not 0 <= limit <= MAX_FILE_BYTES:
            raise ValueError("Cipher limit must be within the product file limit")
        if len(data) > limit:
            raise ValueError("Cipher input exceeds the byte limit")
        result = data
        for step in reversed(self._steps) if reverse else self._steps:
            if (
                step.cipher.id == "polybius-square"
                and not reverse
                and len(result) > limit // 3
            ):
                raise ValueError("Polybius expansion exceeds the byte limit")
            result = step.cipher.method(result, step.params, reverse)
            if len(result) > limit:
                raise ValueError("Cipher intermediate output exceeds the byte limit")
        return result


def catalog() -> dict[str, Any]:
    """Discover exact classical capabilities without disclosing operator recipes."""
    return {
        "schema_version": 1,
        "family": "classical-obfuscation",
        "max_steps": MAX_STEPS,
        "max_bytes": MAX_FILE_BYTES,
        "ciphers": [cipher.document() for cipher in REGISTRY.values()],
    }
