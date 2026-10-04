"""The semantic cipher API used by Vigiles and programmatic encoder clients.

The retained single-pass engine supplies collision handling and exact forward
round trips. This module restores the cipher's public encode/decode interface;
it does not replace the separate BAMb5 authenticated token cipher.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bamboozle.dictionary import Dictionary
from bamboozle.engine import Translator, match_case
from bamboozle.utils import atomic_write, json_bytes, parse_json, read_bytes

__all__ = [
    "CipherEngine",
    "load_json",
    "main",
    "match_case",
    "process_forward",
    "process_reverse",
    "save_json",
]

LITERAL_TEMPLATE = "__SCM_LIT_{}__"
LITERAL_REGEX = r"__SCM_LIT_(.*?)__"


def load_json(file_path: str | Path) -> Any:
    return parse_json(read_bytes(Path(file_path)))


def save_json(data: Any, file_path: str | Path) -> None:
    atomic_write(Path(file_path), json_bytes(data))


class CipherEngine(Translator):
    """Encode domain text for the agent and decode its edited/generated response."""

    def encode(self, text: str) -> str:
        return self.forward(text)

    def decode(self, text: str) -> str:
        return self.reverse(text)


def process_forward(
    text: str,
    cipher_dict: Dictionary | Mapping[str, str],
    input_name: str | None = None,
) -> str:
    """Encode a text/metadata stream; input_name is retained call compatibility."""
    return CipherEngine(cipher_dict).encode(text)


def process_reverse(text: str, cipher_dict: Dictionary | Mapping[str, str]) -> str:
    """Decode the agent's response using the same mapping and literal convention."""
    return CipherEngine(cipher_dict).decode(text)


def main(argv: list[str] | None = None) -> int:
    from bamboozle.translate import main as run

    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
