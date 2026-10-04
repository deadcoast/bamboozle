"""Bamboozle product CLI; the vigiles command remains a compatibility alias."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from bamboozle import __version__, identity, structure
from bamboozle import audit as watch
from bamboozle.dictionary import PROFILES, Dictionary
from bamboozle.fixtures import FixtureError
from bamboozle.formats import format_options
from bamboozle.service import OperationError, prepare, preview, recover, scan, verify
from bamboozle.tessera import create_key, seal, unseal
from bamboozle.transport import transfer_file
from bamboozle.utils import PublicationError, atomic_write, read_bytes
from bamboozle.workspace import Workspace


def _dictionary_arguments(command: argparse.ArgumentParser) -> None:
    selection = command.add_mutually_exclusive_group()
    selection.add_argument("--dict", dest="dictionary")
    selection.add_argument(
        "--profile", choices=PROFILES, help="Bundled vocabulary (default: default)"
    )


def _format_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--protect",
        action="append",
        choices=["paths", "urls"],
        help="Preserve technical literals with --format text; repeat to select both",
    )
    command.add_argument(
        "--format",
        choices=["text", "markdown", "json", "python", "yaml"],
        dest="document_format",
    )
    command.add_argument(
        "--yaml-pointer",
        action="append",
        help="Exact YAML string-value path using / segments and ~0/~1 escapes; include every alias path",
    )
    command.add_argument(
        "--json-pointer",
        action="append",
        help="Exact JSON string-value pointer; repeat for each selected field",
    )
    command.add_argument(
        "--json-values-only",
        action="store_true",
        help="Explicitly select all nested JSON string values; preserve keys and nonstring tokens",
    )
    command.add_argument(
        "--python-identifier",
        action="append",
        help="Exact project identifier spelling; repeat for each selected dictionary entry",
    )
    command.add_argument(
        "--python-module",
        action="append",
        help="Exact project import path whose selected exported members also translate",
    )
    command.add_argument(
        "--python-string",
        action="append",
        help="Exact Python string value selected separately, including symbolic references",
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="bamboozle",
        description="Local translation, authenticated packets, and repository audits.",
    )
    result.add_argument(
        "--version", action="version", version=f"Bamboozle {__version__}"
    )
    result.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Existing workspace directory (default: current directory)",
    )
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "cipher-catalog",
        help="Discover semantic, classical and authenticated cipher capabilities",
    )
    classical = commands.add_parser(
        "cipher-transform",
        help="Review/apply an explicit lossless classical byte recipe",
    )
    classical.add_argument("source")
    classical.add_argument("--recipe", required=True)
    classical.add_argument("-o", "--output", required=True)
    classical.add_argument("--reverse", action="store_true")
    classical.add_argument("--apply", action="store_true")
    classical.add_argument("--review-sha256")
    classical.add_argument("--recover-metadata", action="store_true")
    classical_verify = commands.add_parser(
        "cipher-verify", help="Verify cipher bytes and inverse using a saved receipt"
    )
    classical_verify.add_argument("source")
    classical_verify.add_argument("--receipt")
    for name in ("view-pack", "exchange-open", "exchange-return"):
        exchange = commands.add_parser(
            name, help="Authenticated neutral project exchange"
        )
        exchange.add_argument(
            "source", help="View ID, packet path, or received exchange ID"
        )
        exchange.add_argument("-o", "--output", required=True)
        exchange.add_argument("--key")
        exchange.add_argument("--recipient")
        exchange.add_argument(
            "--peer",
            help="Registered peer: destination when sending, expected sender when receiving",
        )
        exchange.add_argument(
            "--recipe",
            help="Optional private classical recipe before authenticated encryption",
        )
        if name == "exchange-open":
            exchange.add_argument("--recover", action="store_true")
        else:
            exchange.add_argument("--ttl", type=int, default=86400)
    commands.add_parser(
        "exchange-context", help="Show a completed receiver's neutral agent context"
    ).add_argument("exchange_id")
    for name in ("encode", "decode", "preview"):
        command = commands.add_parser(name)
        command.add_argument("source")
        command.add_argument("-o", "--output")
        _dictionary_arguments(command)
        _format_arguments(command)
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
    validate_return = commands.add_parser(
        "validate-return",
        help="Inspect edited neutral text with its original ledger and format rules",
    )
    validate_return.add_argument("source")
    validate_return.add_argument("--original", required=True)
    validate_return.add_argument("--ledger")
    validate_return.add_argument("--offset", type=int, default=0)
    validate_return.add_argument("--max-findings", type=int, default=100)
    fixture_preview = commands.add_parser(
        "fixture-preview",
        help="Generate and check neutral template data without writing files",
    )
    fixture_preview.add_argument("source")
    fixture_preview.add_argument("--rules", required=True)
    fixture_validate = commands.add_parser(
        "fixture-validate",
        help="Validate a record collection against a public prepared fixture contract",
    )
    fixture_validate.add_argument("source")
    fixture_validate.add_argument("--contract", required=True)
    fixture_validate.add_argument(
        "--fixture",
        help="Original fixture name when checking a differently named case document",
    )
    lexicon_export = commands.add_parser(
        "lexicon-export", help="Export real mappings into an editable operator lexicon"
    )
    _dictionary_arguments(lexicon_export)
    lexicon_export.add_argument("-o", "--output", required=True)
    lexicon_export.add_argument("--mapping-version", required=True)
    for name in (
        "lexicon-check",
        "lexicon-query",
        "lexicon-verify",
        "lexicon-edit",
        "lexicon-compile",
    ):
        command = commands.add_parser(name)
        command.add_argument("source")
        if name in ("lexicon-query", "lexicon-compile"):
            command.add_argument("--offset", type=int, default=0)
            command.add_argument("--limit", type=int, default=100)
            command.add_argument("--expected-sha256")
        if name == "lexicon-query":
            command.add_argument("--query", default="")
            command.add_argument(
                "--side", choices=["bam", "boozle", "both"], default="both"
            )
        if name in ("lexicon-edit", "lexicon-compile"):
            command.add_argument("-o", "--output", required=True)
            command.add_argument("--apply", action="store_true")
            command.add_argument("--review-sha256")
            command.add_argument("--recover-metadata", action="store_true")
        if name == "lexicon-edit":
            command.add_argument("--patch", required=True)
        if name == "lexicon-compile":
            command.add_argument(
                "--base", help="Existing dictionary to compare and pin during review"
            )
    inspection = commands.add_parser(
        "scan", help="Inspect dictionary matches without writing files"
    )
    inspection.add_argument("source")
    inspection.add_argument("--max-matches", type=int, default=1000)
    _dictionary_arguments(inspection)
    commands.add_parser("verify-restoration").add_argument("source")
    export = commands.add_parser(
        "view-export", help="Build a scoped Bamboozle development view"
    )
    export.add_argument("config")
    export.add_argument("--output", required=True)
    _dictionary_arguments(export)
    for name in ("view-review", "view-apply", "view-status"):
        command = commands.add_parser(name)
        if name != "view-status":
            command.add_argument("--return-packet")
            command.add_argument("--peer")
            command.add_argument("--key")
            command.add_argument("--recipient")
            command.add_argument("--recipe")
        command.add_argument("view_id")
        if name != "view-status":
            command.add_argument("--delete", dest="delete_files", action="append")
        if name == "view-review":
            command.add_argument("--validation-path")
            command.add_argument("--offset", type=int, default=0)
            command.add_argument("--max-findings", type=int, default=100)
        if name == "view-apply":
            command.add_argument("--review-sha256", required=True)
            command.add_argument("--recover", action="store_true")
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
    _dictionary_arguments(restore)
    _format_arguments(restore)
    restore.add_argument("--apply", action="store_true")
    for name in ("validate", "dictionary-export", "serve", "serve-agent", "doctor"):
        command = commands.add_parser(name)
        _dictionary_arguments(command)
        if name == "serve-agent":
            command.add_argument(
                "--policy", help="Private source-agent document policy"
            )
            command.add_argument(
                "--checks", help="Private named source-agent application checks"
            )
        if name == "dictionary-export":
            command.add_argument("-o", "--output", required=True)
            command.add_argument("--mapping-version")
    commands.add_parser("baseline")
    commands.add_parser("audit")
    structural = commands.add_parser(
        "structure-check", help="Validate explicit local repository structure rules"
    )
    structural.add_argument(
        "--rules", help="Inspect a draft profile instead of .vigiles/structure.json"
    )
    structural.add_argument("--offset", type=int, default=0)
    structural.add_argument("--limit", type=int, default=100)
    structural.add_argument(
        "--expected-sha256", help="Pin pagination to a prior check digest"
    )
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
    commands.add_parser("identity-init").add_argument("identity")
    commands.add_parser("identity-status")
    commands.add_parser("delivery-status").add_argument("delivery_id")
    for action in ("register", "rotate", "revoke"):
        lifecycle = commands.add_parser(f"recipient-{action}")
        lifecycle.add_argument("peer")
        lifecycle.add_argument(
            "--key-id" if action == "revoke" else "--key", required=True
        )
        lifecycle.add_argument("--expected-sha256", required=action != "register")
    commands.add_parser("keygen").add_argument("--key", required=True)
    for name in ("seal", "open"):
        command = commands.add_parser(name)
        command.add_argument("source")
        command.add_argument("-o", "--output", required=True)
        command.add_argument("--key")
        command.add_argument("--recipient")
        command.add_argument("--peer")
        if name == "open":
            command.add_argument("--recover", action="store_true")
        else:
            command.add_argument("--ttl", type=int, default=86400)
    return result


def _mapping(args: argparse.Namespace, workspace: Workspace) -> Dictionary:
    name = getattr(args, "dictionary", None)
    return Dictionary.load(
        workspace.resolve(name) if name else None,
        profile=getattr(args, "profile", None) or "default",
    )


def _emit(report: dict[str, Any], *, compact: bool = False) -> None:
    print(
        json.dumps(
            # ASCII JSON preserves Unicode values across Windows pipe code pages.
            report,
            ensure_ascii=True,
            indent=None if compact else 2,
            allow_nan=False,
        ),
        flush=True,
    )


def _execute(args: argparse.Namespace, workspace: Workspace) -> dict[str, Any]:
    if args.command in (
        "view-pack",
        "exchange-open",
        "exchange-return",
        "exchange-context",
    ):
        from bamboozle.project_exchange import (
            exchange_context,
            open_exchange,
            pack_return,
            pack_view,
        )

        if args.command == "exchange-context":
            return exchange_context(workspace, args.exchange_id)
        if args.command == "exchange-open":
            return open_exchange(
                workspace,
                args.source,
                args.output,
                key=args.key,
                recipient=args.recipient,
                peer=args.peer,
                recipe=args.recipe,
                recover=args.recover,
            )
        pack = pack_view if args.command == "view-pack" else pack_return
        return pack(
            workspace,
            args.source,
            args.output,
            key=args.key,
            recipient=args.recipient,
            peer=args.peer,
            recipe=args.recipe,
            ttl=args.ttl,
        )
    if args.command in ("cipher-catalog", "cipher-transform", "cipher-verify"):
        from bamboozle.cipher_operations import (
            capabilities,
            prepare_transform,
            verify_transform,
        )

        if args.command == "cipher-catalog":
            return capabilities()
        if args.command == "cipher-verify":
            return verify_transform(workspace, args.source, receipt=args.receipt)
        if bool(args.apply) != bool(args.review_sha256):
            raise ValueError(
                "--apply requires --review-sha256 from a successful preview"
            )
        cipher_plan = prepare_transform(
            workspace,
            args.source,
            args.recipe,
            args.output,
            reverse=args.reverse,
            recover_metadata=args.recover_metadata,
        )
        return (
            cipher_plan.apply(
                args.review_sha256, recover_metadata=args.recover_metadata
            )
            if args.apply
            else cipher_plan.report()
        )
    if args.command == "validate-return":
        from bamboozle.return_validation import validate_return

        return validate_return(
            workspace,
            args.source,
            args.original,
            ledger=args.ledger,
            offset=args.offset,
            max_findings=args.max_findings,
        )
    if args.command in ("fixture-preview", "fixture-validate"):
        from bamboozle.fixtures import preview_fixture, validate_fixture

        return (
            preview_fixture(workspace, args.source, args.rules)
            if args.command == "fixture-preview"
            else validate_fixture(
                workspace, args.source, args.contract, fixture=args.fixture
            )
        )
    if args.command.startswith("lexicon-"):
        from bamboozle.lexicon import (
            check_lexicon,
            export_lexicon,
            prepare_compile,
            prepare_edit,
            query_lexicon,
            verify_compilation,
        )

        if args.command == "lexicon-export":
            return export_lexicon(
                workspace, _mapping(args, workspace), args.output, args.mapping_version
            )
        if args.command == "lexicon-check":
            return check_lexicon(workspace, args.source)
        if args.command == "lexicon-verify":
            return verify_compilation(workspace, args.source)
        if args.command == "lexicon-query":
            return query_lexicon(
                workspace,
                args.source,
                query=args.query,
                side=args.side,
                offset=args.offset,
                limit=args.limit,
                expected_sha256=args.expected_sha256,
            )
        if bool(args.apply) != bool(args.review_sha256):
            raise ValueError(
                "--apply requires --review-sha256 from a successful preview"
            )
        compiled = (
            prepare_edit(
                workspace,
                args.source,
                args.patch,
                args.output,
                recover_metadata=args.recover_metadata,
            )
            if args.command == "lexicon-edit"
            else prepare_compile(
                workspace,
                args.source,
                args.output,
                base=args.base,
                offset=args.offset,
                limit=args.limit,
                expected_sha256=args.expected_sha256,
                recover_metadata=args.recover_metadata,
            )
        )
        return (
            compiled.apply(args.review_sha256, recover_metadata=args.recover_metadata)
            if args.apply
            else compiled.report()
        )
    if args.command.startswith("view-"):
        from bamboozle.project_views import export_view
        from bamboozle.view_import import apply_import, review_import, view_status

        if args.command == "view-export":
            private = (workspace.resolve(args.dictionary),) if args.dictionary else ()
            return export_view(
                workspace,
                args.config,
                args.output,
                _mapping(args, workspace),
                private_paths=private,
            )
        if args.command == "view-review":
            return review_import(
                workspace,
                args.view_id,
                delete_files=args.delete_files,
                validation_path=args.validation_path,
                validation_offset=args.offset,
                max_findings=args.max_findings,
                return_packet=args.return_packet,
                key=args.key,
                recipient=args.recipient,
                peer=args.peer,
                recipe=args.recipe,
            )
        if args.command == "view-status":
            return view_status(workspace, args.view_id)
        return apply_import(
            workspace,
            args.view_id,
            args.review_sha256,
            delete_files=args.delete_files,
            recover=args.recover,
            return_packet=args.return_packet,
            key=args.key,
            recipient=args.recipient,
            peer=args.peer,
            recipe=args.recipe,
        )
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
            dictionary=_mapping(args, workspace) if args.profile else args.dictionary,
            ledger=getattr(args, "ledger", None),
            strict=getattr(args, "strict", False),
            format_rules=format_options(
                args.document_format,
                args.json_pointer,
                args.python_identifier,
                args.python_module,
                args.python_string,
                args.yaml_pointer,
                args.protect,
                args.json_values_only,
            ),
        )
        return preview(plan) if args.command == "preview" else plan.apply()
    if args.command == "scan":
        return scan(
            workspace,
            args.source,
            _mapping(args, workspace),
            max_matches=args.max_matches,
        )
    if args.command == "verify":
        return verify(workspace, args.source)
    if args.command == "recover":
        return recover(
            workspace,
            args.source,
            args.original,
            _mapping(args, workspace),
            apply=args.apply,
            format_rules=format_options(
                args.document_format,
                args.json_pointer,
                args.python_identifier,
                args.python_module,
                args.python_string,
                args.yaml_pointer,
                args.protect,
                args.json_values_only,
            ),
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
    if args.command == "structure-check":
        return structure.check(
            workspace,
            args.rules,
            offset=args.offset,
            limit=args.limit,
            expected_sha256=args.expected_sha256,
        )
    if args.command == "identity-init":
        return identity.initialize(workspace, args.identity)
    if args.command == "identity-status":
        return identity.status(workspace)
    if args.command == "delivery-status":
        return identity.delivery_status(workspace, args.delivery_id)
    if args.command.startswith("recipient-"):
        return identity.change_peer(
            workspace,
            args.peer,
            action=args.command.removeprefix("recipient-"),
            key=getattr(args, "key", None),
            key_id=getattr(args, "key_id", None),
            expected_sha256=args.expected_sha256,
        )
    if args.command == "keygen":
        path = create_key(workspace, args.key)
        return {
            "schema_version": 1,
            "status": "key_created",
            "key": str(path.relative_to(workspace.root)),
        }
    if args.command in ("seal", "open"):
        identity.select_mode(args.key, args.recipient, args.peer)
        if args.peer is not None:
            return transfer_file(
                workspace,
                args.source,
                args.output,
                peer=args.peer,
                opening=args.command == "open",
                recover=getattr(args, "recover", False),
                ttl=getattr(args, "ttl", 86400),
            )
        if getattr(args, "recover", False) or getattr(args, "ttl", 86400) != 86400:
            raise ValueError(
                "Recovery and TTL controls require managed --peer transport"
            )
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
        guard = structure.prepare_guard(
            workspace, {destination.relative_to(workspace.root).as_posix(): content}
        )
        report = {
            "schema_version": 1,
            "status": "sealed" if args.command == "seal" else "opened",
            "recipient": args.recipient,
            "output": str(destination.relative_to(workspace.root)),
            "output_bytes": len(content),
            "output_saved": False,
            **({"structure": guard.certificate} if guard.certificate else {}),
        }
        try:
            guard.recheck()
            atomic_write(destination, content)
            report["output_saved"] = True
            guard.recheck()
        except (OSError, ValueError) as exc:
            report.update(
                status="error",
                error=str(exc),
                output_saved=report["output_saved"]
                or isinstance(exc, PublicationError),
            )
            raise OperationError(str(exc), report) from exc
        return report
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
                    workspace.root,
                    _mapping(args, workspace),
                    private_paths=private,
                    policy=args.policy,
                    checks=args.checks,
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
    except FixtureError as exc:
        _emit(exc.report)
        return 3
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
