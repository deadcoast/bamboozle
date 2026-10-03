"""Native promotion, quarantine, and explicit interrupted-operation recovery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from workshop import config, operations, plans


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=root or config.WORKSHOP)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--candidate")
    actions.add_argument("--quarantine")
    actions.add_argument("--recover")
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--prepare-candidate")
    actions.add_argument("--prepare-quarantine")
    actions.add_argument("--show-plan")
    actions.add_argument("--apply-plan")
    actions.add_argument("--cancel-plan")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--reason", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.apply_plan or args.cancel_plan) != bool(args.plan_sha256):
        parser.error(
            "--plan-sha256 is required only with --apply-plan or --cancel-plan"
        )
    if args.details and not args.show_plan:
        parser.error("--details requires --show-plan")
    if args.reason and not (args.quarantine or args.prepare_quarantine):
        parser.error("--reason requires --quarantine or --prepare-quarantine")
    if args.dry_run and (
        args.prepare_candidate
        or args.prepare_quarantine
        or args.show_plan
        or args.cancel_plan
        or args.status
    ):
        parser.error(
            "--dry-run applies only to direct operations, recovery, or --apply-plan"
        )
    try:
        if args.prepare_candidate:
            result = plans.prepare_promotion(args.root, args.prepare_candidate)
        elif args.prepare_quarantine:
            result = plans.prepare_quarantine(
                args.root, args.prepare_quarantine, args.reason
            )
        elif args.show_plan:
            result = plans.inspect_plan(args.root, args.show_plan, details=args.details)
        elif args.apply_plan:
            result = plans.apply_plan(
                args.root, args.apply_plan, args.plan_sha256, dry_run=args.dry_run
            )
        elif args.cancel_plan:
            result = plans.cancel_plan(args.root, args.cancel_plan, args.plan_sha256)
        elif args.candidate:
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
    except plans.PreparedPlanSaved as exc:
        print(json.dumps(exc.report))
        return 1
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
