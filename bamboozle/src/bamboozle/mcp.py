"""A scoped stdio MCP interface with explicit preview/application separation."""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from bamboozle.agent_files import AgentFiles
from bamboozle.audit import audit
from bamboozle.dictionary import Dictionary
from bamboozle.migration import prepare_migration
from bamboozle.restoration import ReviewedFilePlan, prepare_restore, verify_restoration
from bamboozle.scm_mcp import register_tools
from bamboozle.service import (
    ConversionPlan,
    OperationError,
    ledger_path,
    prepare,
    preview,
    verify,
)
from bamboozle.workspace import Workspace

PLAN_TTL = 600
MAX_PLANS = 32


def create_server(
    root: Path,
    dictionary: Dictionary | None = None,
    *,
    private_paths: tuple[Path, ...] = (),
) -> FastMCP:
    workspace = Workspace(root)
    mapping = dictionary if dictionary is not None else Dictionary.load()
    server = FastMCP("vigiles")
    register_tools(server, AgentFiles(workspace, mapping, private_paths=private_paths))
    plans: OrderedDict[str, tuple[float, ConversionPlan | ReviewedFilePlan, bool]] = (
        OrderedDict()
    )
    lock = threading.Lock()

    def retain(
        plan: ConversionPlan | ReviewedFilePlan,
        report: dict[str, Any],
        recover_metadata: bool = False,
    ) -> dict[str, Any]:
        if report.get("ok") is False:
            return report
        token = secrets.token_urlsafe(24)
        with lock:
            now = time.monotonic()
            for stale in [
                key
                for key, (created, _, _) in plans.items()
                if now - created >= PLAN_TTL
            ]:
                plans.pop(stale)
            while len(plans) >= MAX_PLANS:
                plans.popitem(last=False)
            plans[token] = (now, plan, recover_metadata)
        return {**report, "plan_id": token, "expires_in_seconds": PLAN_TTL}

    @server.tool(annotations={"readOnlyHint": True})
    def dictionary_status() -> dict[str, Any]:
        """Describe the validated mapping without returning its terms."""
        return {
            "entries": len(mapping),
            "version": mapping.version,
            "sha256": mapping.fingerprint,
        }

    @server.tool(annotations={"readOnlyHint": True})
    def preview_file(
        source: str,
        output: str | None = None,
        reverse: bool = False,
        ledger: str | None = None,
        strict: bool = False,
        dictionary: str | None = None,
    ) -> dict[str, Any]:
        """Preview source or agent output. Reverse uses its saved mapping when present.

        For newly named output, select the originating ledger or a dictionary file.
        Without either, use the server mapping. Strict requires unchanged encoding.
        Unmarked target terms translate; __SCM_LIT_...__ markers preserve literals.
        """
        selected: Dictionary | str | None = dictionary
        has_saved_mapping = reverse and (
            ledger is not None or ledger_path(workspace.resolve(source)).exists()
        )
        if selected is None and not has_saved_mapping:
            selected = mapping
        plan = prepare(
            workspace,
            source,
            output=output,
            mode="reverse" if reverse else "forward",
            dictionary=selected,
            ledger=ledger,
            strict=strict,
        )
        return retain(plan, preview(plan))

    @server.tool(annotations={"readOnlyHint": True})
    def preview_restoration(
        source: str,
        original: str,
        output: str | None = None,
        ledger: str | None = None,
        terms: str = "review",
        decisions: str | None = None,
        recover_metadata: bool = False,
    ) -> dict[str, Any]:
        """Review edited source restoration; unresolved terms never get an apply token."""
        plan = prepare_restore(
            workspace,
            source,
            original,
            output=output,
            ledger=ledger,
            terms=terms,
            decisions=decisions,
            recover_metadata=recover_metadata,
        )
        return retain(plan, plan.report(), recover_metadata)

    @server.tool(annotations={"readOnlyHint": True})
    def preview_migration(
        source: str,
        original: str,
        ledger: str,
        output: str | None = None,
        dictionary: str | None = None,
        recover_metadata: bool = False,
    ) -> dict[str, Any]:
        """Review a verified historical pair's migration into separate native files."""
        plan = prepare_migration(
            workspace,
            source,
            original,
            ledger=ledger,
            output=output,
            dictionary=dictionary,
            recover_metadata=recover_metadata,
        )
        return retain(plan, plan.report(), recover_metadata)

    @server.tool(annotations={"readOnlyHint": True})
    def verify_restored_file(source: str) -> dict[str, Any]:
        """Verify restored output and the retained input hashes in its provenance receipt."""
        return verify_restoration(workspace, source)

    @server.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        }
    )
    def apply_preview(plan_id: str) -> dict[str, Any]:
        """Publish a previously previewed result; refuses changed inputs and existing files."""
        with lock:
            saved = plans.pop(plan_id, None)
        if saved is None or time.monotonic() - saved[0] >= PLAN_TTL:
            raise ValueError("Unknown, expired, or already consumed preview")
        plan = saved[1]
        try:
            if isinstance(plan, ReviewedFilePlan):
                return plan.apply(plan.review_sha256, recover_metadata=saved[2])
            return plan.apply()
        except OperationError as exc:
            return {**exc.report, "ok": False}

    @server.tool(annotations={"readOnlyHint": True})
    def verify_file(source: str) -> dict[str, Any]:
        """Verify an encoded file against its saved mapping and content digests."""
        return verify(workspace, source)

    @server.tool(annotations={"readOnlyHint": True})
    def audit_workspace() -> dict[str, Any]:
        """Report structural changes against the explicitly created local baseline."""
        return audit(workspace)

    return server
