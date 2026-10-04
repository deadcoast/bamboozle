"""Terminal entry point for the Vigiles middleware framework."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from bamboozle import __version__
from bamboozle import audit as watch
from bamboozle.dictionary import Dictionary
from bamboozle.service import OperationError, prepare, preview, recover, verify
from bamboozle.tessera import create_key, seal, unseal
from bamboozle.utils import PublicationError, atomic_write, read_bytes
from bamboozle.workspace import Workspace


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="vigiles",
        description="Local translation, authenticated packets, and repository audits.",
    )
    result.add_argument(
        "--version", action="version", version=f"Vigiles / Bamboozle {__version__}"
    )
    result.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Existing workspace directory (default: current directory)",
    )
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("encode", "decode", "preview"):
        command = commands.add_parser(name)
        command.add_argument("source")
        command.add_argument("-o", "--output")
        command.add_argument("--dict", dest="dictionary")
        if name != "encode":
            command.add_argument(
                "--ledger", help="Saved mapping for a returned agent file"
            )
            command.add_argument(
                "--strict",
                action="store_true",
                help="Require an unchanged encoded file and exact original restoration",
            )
        if name == "preview":
            command.add_argument("--reverse", action="store_true")
    commands.add_parser("verify").add_argument("source")
    commands.add_parser("verify-restoration").add_argument("source")
    for name in ("restore-edits", "migrate-ledger"):
        command = commands.add_parser(name)
        command.add_argument("source")
        command.add_argument("--original", required=True)
        command.add_argument("--ledger", required=name == "migrate-ledger")
        command.add_argument("-o", "--output")
        command.add_argument("--apply", action="store_true")
        command.add_argument("--review-sha256")
        command.add_argument("--recover-metadata", action="store_true")
        if name == "restore-edits":
            command.add_argument(
                "--terms", choices=["review", "translate", "literal"], default="review"
            )
            command.add_argument("--decisions")
        else:
            command.add_argument("--dict", dest="dictionary")
    restore = commands.add_parser("recover")
    restore.add_argument("source")
    restore.add_argument("--original", required=True)
    restore.add_argument("--dict", dest="dictionary")
    restore.add_argument("--apply", action="store_true")
    for name in ("validate", "dictionary-export", "serve", "serve-agent", "doctor"):
        command = commands.add_parser(name)
        command.add_argument("--dict", dest="dictionary")
        if name == "dictionary-export":
            command.add_argument("-o", "--output", required=True)
            command.add_argument("--mapping-version")
    commands.add_parser("baseline")
    commands.add_parser("audit")
    workshop = commands.add_parser(
        "workshop-check",
        help="Inspect external pipeline dependencies without execution",
    )
    workshop.add_argument("--workshop", type=Path)
    workshop.add_argument("--python", type=Path)
    initialize = commands.add_parser(
        "workshop-init", help="Initialize a new native workshop"
    )
    initialize.add_argument("--workshop", type=Path)
    continuous = commands.add_parser("watch")
    continuous.add_argument("--interval", type=float, default=5.0)
    continuous.add_argument(
        "--count", type=int, help="Number of audits; omit to run until Ctrl+C"
    )
    commands.add_parser("keygen").add_argument("--key", required=True)
    for name in ("seal", "open"):
        command = commands.add_parser(name)
        command.add_argument("source")
        command.add_argument("-o", "--output", required=True)
        command.add_argument("--key", required=True)
        command.add_argument("--recipient", required=True)
    return result


def _mapping(args: argparse.Namespace, workspace: Workspace) -> Dictionary:
    name = getattr(args, "dictionary", None)
    return Dictionary.load(workspace.resolve(name) if name else None)


def _emit(report: dict[str, Any], *, compact: bool = False) -> None:
    print(
        json.dumps(
            report, ensure_ascii=False, indent=None if compact else 2, allow_nan=False
        ),
        flush=True,
    )


def _execute(args: argparse.Namespace, workspace: Workspace) -> dict[str, Any]:
    if args.command == "verify-restoration":
        from bamboozle.restoration import verify_restoration

        return verify_restoration(workspace, args.source)
    if args.command in ("restore-edits", "migrate-ledger"):
        from bamboozle.migration import prepare_migration
        from bamboozle.restoration import prepare_restore

        if bool(args.apply) != bool(args.review_sha256):
            raise ValueError(
                "--apply requires --review-sha256 from a successful preview"
            )
        common = {
            "output": args.output,
            "ledger": args.ledger,
            "recover_metadata": args.recover_metadata,
        }
        reviewed = (
            prepare_restore(
                workspace,
                args.source,
                args.original,
                terms=args.terms,
                decisions=args.decisions,
                **common,
            )
            if args.command == "restore-edits"
            else prepare_migration(
                workspace,
                args.source,
                args.original,
                dictionary=args.dictionary,
                **common,
            )
        )
        return (
            reviewed.apply(args.review_sha256, recover_metadata=args.recover_metadata)
            if args.apply
            else reviewed.report()
        )
    if args.command == "workshop-init":
        from workshop import config
        from workshop.bootstrap import initialize

        root = args.workshop if args.workshop is not None else config.WORKSHOP
        return initialize(root)
    if args.command == "workshop-check":
        from workshop import config
        from workshop.pipeline import inspect_pipeline

        root = args.workshop if args.workshop is not None else config.WORKSHOP
        python = args.python if args.python is not None else config.PYTHON
        return inspect_pipeline(root / "scrape", python)
    if args.command in ("encode", "decode", "preview"):
        reverse = args.command == "decode" or getattr(args, "reverse", False)
        plan = prepare(
            workspace,
            args.source,
            output=args.output,
            mode="reverse" if reverse else "forward",
            dictionary=args.dictionary,
            ledger=getattr(args, "ledger", None),
            strict=getattr(args, "strict", False),
        )
        return preview(plan) if args.command == "preview" else plan.apply()
    if args.command == "verify":
        return verify(workspace, args.source)
    if args.command == "recover":
        return recover(
            workspace,
            args.source,
            args.original,
            _mapping(args, workspace),
            apply=args.apply,
        )
    if args.command in ("validate", "dictionary-export", "doctor"):
        dictionary = _mapping(args, workspace)
        if args.command == "dictionary-export":
            if args.mapping_version is not None:
                dictionary = Dictionary(dictionary, args.mapping_version)
            dictionary.save(workspace.resolve(args.output, write=True))
        report: dict[str, Any] = {
            "schema_version": 1,
            "status": "valid",
            "entries": len(dictionary),
            "mapping_version": dictionary.version,
            "dictionary_sha256": dictionary.fingerprint,
        }
        if args.command == "doctor":
            report.update(
                version=__version__,
                root=str(workspace.root),
                vigiles_spec_present=(
                    workspace.root / ".mivim/mivim.vigiles.md"
                ).is_file(),
                baseline_present=(workspace.root / watch.BASELINE).is_file(),
            )
        return report
    if args.command == "baseline":
        return watch.baseline(workspace)
    if args.command == "audit":
        return watch.audit(workspace)
    if args.command == "keygen":
        path = create_key(workspace, args.key)
        return {
            "schema_version": 1,
            "status": "key_created",
            "key": str(path.relative_to(workspace.root)),
        }
    if args.command in ("seal", "open"):
        source = workspace.resolve(args.source)
        destination = workspace.resolve(args.output, write=True)
        key_path = workspace.resolve(args.key)
        if source == key_path or destination in (source, key_path):
            raise ValueError("Input, output and key must use distinct paths")
        key = read_bytes(key_path, 32)
        if args.command == "seal":
            content = seal(workspace.read(source), key, args.recipient)
        else:
            content = unseal(
                read_bytes(source, workspace.max_bytes * 2), key, args.recipient
            )
        atomic_write(destination, content)
        return {
            "schema_version": 1,
            "status": "sealed" if args.command == "seal" else "opened",
            "recipient": args.recipient,
            "output": str(destination.relative_to(workspace.root)),
            "output_bytes": len(content),
            "output_saved": True,
        }
    raise ValueError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        workspace = Workspace(args.root)
        if args.command in ("serve", "serve-agent"):
            private = (workspace.resolve(args.dictionary),) if args.dictionary else ()
            if args.command == "serve-agent":
                from bamboozle.scm_mcp import create_server as agent_server

                server = agent_server(
                    workspace.root, _mapping(args, workspace), private_paths=private
                )
            else:
                from bamboozle.mcp import create_server

                server = create_server(
                    workspace.root, _mapping(args, workspace), private_paths=private
                )
            server.run(transport="stdio")
            return 0
        if args.command == "watch":
            if not 0.1 <= args.interval <= 86400 or (
                args.count is not None and args.count < 1
            ):
                raise ValueError(
                    "Watch interval must be 0.1-86400 seconds and count must be positive"
                )
            completed = 0
            while args.count is None or completed < args.count:
                _emit(watch.audit(workspace), compact=True)
                completed += 1
                if args.count is None or completed < args.count:
                    time.sleep(args.interval)
            return 0
        report = _execute(args, workspace)
        _emit(report)
        return 3 if report.get("ok") is False else 0
    except OperationError as exc:
        _emit(exc.report)
        return 1
    except (OSError, ValueError, TypeError) as exc:
        _emit(
            {
                "schema_version": 1,
                "status": "error",
                "error": str(exc),
                "output_saved": isinstance(exc, PublicationError),
            }
        )
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
