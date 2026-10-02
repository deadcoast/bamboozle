"""A scoped stdio MCP interface with explicit preview/application separation."""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from bamboozle.audit import audit
from bamboozle.dictionary import Dictionary
from bamboozle.service import ConversionPlan, prepare, preview, verify
from bamboozle.workspace import Workspace

PLAN_TTL = 600
MAX_PLANS = 32


def create_server(root: Path, dictionary: Dictionary | None = None) -> FastMCP:
    workspace = Workspace(root)
    mapping = dictionary if dictionary is not None else Dictionary.load()
    server = FastMCP("vigiles")
    plans: OrderedDict[str, tuple[float, ConversionPlan]] = OrderedDict()
    lock = threading.Lock()

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
        source: str, output: str | None = None, reverse: bool = False
    ) -> dict[str, Any]:
        """Preview a translation inside the configured workspace without file writes."""
        plan = prepare(
            workspace,
            source,
            output=output,
            mode="reverse" if reverse else "forward",
            dictionary=mapping,
        )
        report = preview(plan)
        token = secrets.token_urlsafe(24)
        with lock:
            now = time.monotonic()
            for stale in [
                key for key, (created, _) in plans.items() if now - created >= PLAN_TTL
            ]:
                plans.pop(stale)
            while len(plans) >= MAX_PLANS:
                plans.popitem(last=False)
            plans[token] = (now, plan)
        report.update(plan_id=token, expires_in_seconds=PLAN_TTL)
        return report

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
        return saved[1].apply()

    @server.tool(annotations={"readOnlyHint": True})
    def verify_file(source: str) -> dict[str, Any]:
        """Verify an encoded file against its saved mapping and content digests."""
        return verify(workspace, source)

    @server.tool(annotations={"readOnlyHint": True})
    def audit_workspace() -> dict[str, Any]:
        """Report structural changes against the explicitly created local baseline."""
        return audit(workspace)

    return server
