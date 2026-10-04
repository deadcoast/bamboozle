"""Original cipher flags and translation API over the shared engine."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bamboozle.cipher import process_forward, process_reverse
from bamboozle.dictionary import Dictionary
from bamboozle.engine import Translator, match_case
from bamboozle.service import prepare
from bamboozle.utils import atomic_write, json_bytes, parse_json, read_bytes
from bamboozle.workspace import Workspace

__all__ = [
    "Bamboozletranslate",
    "load_json",
    "main",
    "process_forward",
    "process_reverse",
    "save_json",
]

LITERAL_TEMPLATE = "__SCM_LIT_{}__"
LITERAL_REGEX = r"__SCM_LIT_(.*?)__"
LEDGER_FILE = (
    ".scm_ledger.json"  # Historical name; new operations use per-file ledgers.
)


class Bamboozletranslate(Translator):
    """Backward-compatible name for the shared, round-trip-checked translator."""

    def __init__(self, dictionary: Mapping[str, str]):
        super().__init__(dictionary)
        self.physics_terms = sorted(set(dictionary.values()), key=len, reverse=True)
        self.explicit_terms = sorted(dictionary, key=len, reverse=True)

    @staticmethod
    def match_case(original: str, target: str) -> str:
        return match_case(original, target)


def load_json(file_path: str | Path) -> Any:
    return parse_json(read_bytes(Path(file_path)))


def save_json(data: Any, file_path: str | Path) -> None:
    atomic_write(Path(file_path), json_bytes(data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Semantic cipher: encode source and decode agent output, "
            "as files or streams."
        )
    )
    parser.add_argument("--mode", choices=["forward", "reverse"], required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dict", dest="dictionary", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        workspace = Workspace(args.root)
        if args.input != "-" and args.output != "-":
            report = prepare(
                workspace,
                args.input,
                mode=args.mode,
                output=args.output,
                dictionary=args.dictionary,
                ledger=args.ledger,
                strict=args.strict,
            ).apply()
            print(json.dumps(report), file=sys.stderr)
            return 0
        if args.ledger is not None or args.strict:
            raise ValueError("--ledger and --strict require file-to-file translation")
        dictionary = Dictionary.load(
            workspace.resolve(args.dictionary) if args.dictionary else None
        )
        original = (
            sys.stdin.read()
            if args.input == "-"
            else workspace.read(args.input).decode("utf-8")
        )
        converted = Translator(dictionary).transform(original, args.mode).text
        if args.output == "-":
            sys.stdout.write(converted)
        else:
            atomic_write(
                workspace.resolve(args.output, write=True), converted.encode("utf-8")
            )
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"Translation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
