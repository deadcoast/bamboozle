"""Bamboozle's terminal commands and guided preview/save workflow."""

import argparse
import os
import sys
from pathlib import Path

if __package__:
    from . import __version__, cipher
else:
    import cipher
    from __init__ import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        prog="bamboozle",
        description="Preview, encode, decode, and verify local text files.",
        epilog="Run without arguments for the guided terminal menu. Paths with spaces need quotes.",
    )
    parser.add_argument("--version", action="version", version=f"Bamboozle {__version__}")
    parser.add_argument(
        "--dict", type=Path, help="Mapping JSON (restore uses the saved ledger by default)"
    )
    parser.add_argument("--json", action="store_true", help="Print one JSON execution report")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, help_text in (
        ("encode", "Save a translated copy and its ledger"),
        ("decode", "Restore an encoded or edited file"),
        ("preview", "Show a diff without writing files"),
        ("verify", "Check the encoded and restored hashes without writes"),
        ("recover", "Recreate a missing ledger from matching original and encoded files"),
        ("validate", "Check the dictionary without reading source files"),
        ("interactive", "Open the guided terminal menu"),
    ):
        command = commands.add_parser(name, help=help_text, description=help_text)
        command.add_argument(
            "--dict",
            type=Path,
            default=argparse.SUPPRESS,
            help="Mapping JSON (restore uses the saved ledger by default)",
        )
        command.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Print one JSON execution report",
        )
        if name not in ("validate", "interactive"):
            command.add_argument(
                "file", type=Path, help="Input file (quote paths containing spaces)"
            )
        if name in ("encode", "decode", "preview"):
            command.add_argument("-o", "--output", type=Path, help="Choose the destination path")
            command.add_argument(
                "--context",
                type=cipher.nonnegative_int,
                help="Show a diff with N context lines; implies preview without saving",
            )
        if name in ("encode", "decode"):
            command.add_argument(
                "--force", action="store_true", help="Replace an existing destination"
            )
            command.add_argument(
                "--preview",
                "--diff",
                action="store_true",
                help="Show a diff without saving",
            )
            command.add_argument(
                "--dry-run", action="store_true", help="Preview counts without saving"
            )
        if name == "preview":
            command.add_argument("--reverse", action="store_true", help="Preview decoding")
        if name in ("decode", "preview"):
            command.add_argument(
                "--strict", action="store_true", help="Require unchanged encoded bytes"
            )
        if name == "decode":
            command.add_argument(
                "--keep-ledger", action="store_true", help="Retain the encoded ledger"
            )
        if name == "recover":
            command.add_argument(
                "--original", type=Path, required=True, help="Matching unencoded original file"
            )
            action = command.add_mutually_exclusive_group()
            action.add_argument(
                "--apply", action="store_true", help="Create only the missing ledger after checks"
            )
            action.add_argument(
                "--dry-run", action="store_true", help="Preview recovery without writes (default)"
            )
    return parser


def report_error(error, report, as_json=False):
    if report.get("mode") == "recover":
        report.setdefault("ledger_saved", False)
    if as_json:
        report.update(
            status="error", error=str(error), output_saved=report.get("output_saved", False)
        )
        cipher.emit_report(report, True)
    elif report.get("ledger_saved"):
        print(
            f"[!] Ledger saved to {report['ledger']}, but finalization failed: {error}",
            file=sys.stderr,
        )
    elif report.get("output_saved"):
        print(
            f"[!] Output saved to {report['output']}, but finalization failed: {error}",
            file=sys.stderr,
        )
    else:
        print(f"[!] {error}", file=sys.stderr)


def entered_path(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return Path(os.path.expandvars(value)).expanduser()


def prompt_file():
    while True:
        value = input("File path (Enter to go back): ").strip()
        if not value:
            return None
        source = entered_path(value)
        try:
            if source.is_file():
                return source
            print(f"[!] Choose an existing file: {source}", file=sys.stderr)
        except OSError as error:
            report_error(error, {})


def show_preview(plan):
    report = plan["report"]
    print(f"\nInput:  {report['input']}\nOutput: {report['output']}")
    print(cipher.describe_dictionary(report))
    print(cipher.describe_counts(report["counts"]) + ".")
    if report["integrity"] == "edited":
        print("This file was edited after encoding. Review the restored changes before saving.")
    elif report["integrity"] in ("legacy_ledger", "missing_ledger"):
        print("No version-2 integrity record is available; original byte hashes cannot be checked.")
    lines = report["diff"].split("\n")
    if not lines[-1]:
        lines.pop()
    if lines:
        print("\n".join(lines[:60]))
        if len(lines) > 60:
            print(f"Showing 60 of {len(lines)} diff lines. Choose F to show the full diff.")
    else:
        print("No text changes.")
    existing = [plan["output_path"]]
    if report["mode"] == "forward":
        existing.append(plan["ledger_path"])
    occupied = [path for path in existing if plan["snapshot"][path] is not None]
    for path in occupied:
        print(f"Existing destination: {path}")
    return bool(occupied)


def interactive(dict_path=None):
    print("BAMBOOZLE\nPreview changes, then save when ready. Paste quoted or unquoted file paths.")
    while True:
        print("\n[E] Encode   [D] Decode   [V] Verify   [Q] Quit")
        action = input("Action: ").strip().lower()
        if action in ("q", "quit", "exit"):
            return 0
        modes = {
            "e": "forward",
            "encode": "forward",
            "d": "reverse",
            "decode": "reverse",
            "v": "verify",
            "verify": "verify",
        }
        if action not in modes:
            print("Choose E, D, V, or Q.")
            continue
        source = prompt_file()
        if source is None:
            continue
        mode = modes[action]
        output = None
        while True:
            report = {"schema_version": 1, "mode": mode}
            try:
                plan = cipher.prepare_conversion(mode, source, output, dict_path)
                report = plan["report"]
                if mode == "verify":
                    cipher.emit_report(report, False)
                    break
                cipher.add_preview_diff(plan)
                occupied = show_preview(plan)
                while True:
                    choice = (
                        input(
                            "[S] Save  [F] Full diff  [O] Output path  "
                            "[R] Refresh  [C] Cancel (Enter): "
                        )
                        .strip()
                        .lower()
                    )
                    if choice in ("", "c", "cancel"):
                        print("Cancelled; no files written.")
                        break
                    if choice in ("f", "diff"):
                        print(report["diff"] or "No text changes.")
                        continue
                    if choice in ("r", "refresh"):
                        break
                    if choice in ("o", "output"):
                        destination = input("Output path (Enter to keep current): ").strip()
                        if destination:
                            output = entered_path(destination)
                            break
                        continue
                    if choice in ("s", "save"):
                        if (
                            occupied
                            and input("Type OVERWRITE to replace the shown destination: ")
                            != "OVERWRITE"
                        ):
                            print("Not saved. The preview remains available.")
                            continue
                        cipher.apply_conversion(plan, overwrite=occupied)
                        # Avoid printing the diff a second time after publication.
                        report.pop("diff", None)
                        cipher.emit_report(report, False)
                        break
                    print("Choose S, F, O, R, or C.")
                if choice in ("r", "refresh"):
                    continue
                if choice in ("o", "output") and destination:
                    continue
                break
            except (OSError, UnicodeError, ValueError) as error:
                report_error(error, report)
                break
            except KeyboardInterrupt:
                if report.get("output_saved"):
                    report_error("Interrupted during finalization", report)
                raise


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        if args.json:
            parser.error("Choose a command when using --json")
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            parser.print_help()
            return 0
        args.command = "interactive"
    if args.command == "interactive" and args.json:
        parser.error("--json is available with explicit file or dictionary commands")
    if args.command == "preview" and args.strict and not args.reverse:
        parser.error("--strict requires preview --reverse")
    report = {"schema_version": 1, "mode": args.command}
    try:
        if args.command == "interactive":
            return interactive(args.dict)
        if args.command == "recover":
            plan = cipher.prepare_recovery(args.file, args.original, args.dict)
            report = plan["report"]
            if args.apply:
                cipher.apply_recovery(plan)
            cipher.emit_report(report, args.json)
            return 0
        mode = {"encode": "forward", "decode": "reverse"}.get(args.command, args.command)
        if mode == "preview":
            mode = "reverse" if args.reverse else "forward"
        report["mode"] = mode
        plan = cipher.prepare_conversion(
            mode,
            getattr(args, "file", None),
            getattr(args, "output", None),
            args.dict,
            strict=getattr(args, "strict", False),
            keep_ledger=getattr(args, "keep_ledger", False),
        )
        report = plan["report"]
        context = getattr(args, "context", None)
        is_preview = (
            args.command == "preview" or getattr(args, "preview", False) or context is not None
        )
        if is_preview:
            cipher.add_preview_diff(plan, context=3 if context is None else context)
        if mode in ("forward", "reverse") and not (is_preview or getattr(args, "dry_run", False)):
            cipher.apply_conversion(plan, overwrite=args.force)
        cipher.emit_report(report, args.json)
        return 0
    except EOFError:
        print("\nClosed.")
        return 0
    except KeyboardInterrupt:
        report_error("Interrupted.", report, args.json)
        return 130
    except (OSError, UnicodeError, ValueError) as error:
        report_error(error, report, args.json)
        return 1


if __name__ == "__main__":
    sys.exit(main())
