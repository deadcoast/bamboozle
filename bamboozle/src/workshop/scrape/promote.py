"""Native promotion, quarantine, and explicit interrupted-operation recovery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from workshop import config, operations


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=root or config.WORKSHOP)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--candidate")
    actions.add_argument("--quarantine")
    actions.add_argument("--recover")
    actions.add_argument("--status", action="store_true")
    parser.add_argument("--reason", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.candidate:
            result = operations.promote(args.root, args.candidate, dry_run=args.dry_run)
        elif args.quarantine:
            result = operations.quarantine(
                args.root, args.quarantine, args.reason, dry_run=args.dry_run
            )
        elif args.recover:
            result = operations.recover(args.root, args.recover, dry_run=args.dry_run)
        else:
            result = operations.pipeline_status(args.root)
        print(json.dumps(result, indent=2))
        return 0
    except operations.PendingOperation as exc:
        print(
            json.dumps(
                {
                    "status": "recovery_required",
                    "operation_id": exc.operation_id,
                    "error": str(exc),
                }
            )
        )
        return 1
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
