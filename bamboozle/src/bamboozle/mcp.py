"""A scoped stdio MCP interface with explicit preview/application separation."""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from bamboozle import identity
from bamboozle.agent_files import AgentFiles
from bamboozle.audit import audit
from bamboozle.cipher_operations import (
    capabilities,
    prepare_transform,
    verify_transform,
)
from bamboozle.dictionary import Dictionary
from bamboozle.fixtures import FixtureError, preview_fixture, validate_fixture
from bamboozle.formats import format_options
from bamboozle.lexicon import (
    check_lexicon,
    export_lexicon,
    prepare_compile,
    prepare_edit,
    query_lexicon,
    verify_compilation,
)
from bamboozle.migration import prepare_migration
from bamboozle.project_exchange import (
    exchange_context,
    open_exchange,
    pack_return,
    pack_view,
)
from bamboozle.project_views import export_view
from bamboozle.restoration import ReviewedFilePlan, prepare_restore, verify_restoration
from bamboozle.return_validation import validate_return
from bamboozle.scm_mcp import register_tools
from bamboozle.service import (
    ConversionPlan,
    OperationError,
    ledger_path,
    prepare,
    preview,
    scan,
    verify,
)
from bamboozle.structure import check as check_structure
from bamboozle.transport import transfer_file
from bamboozle.utils import parse_json
from bamboozle.view_import import apply_import, review_import, view_status
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
    server = FastMCP(
        "bamboozle_operator",
        instructions="Operator interface for Bamboozle. Previews, return validation and reviews may show original-domain text. Select explicit document rules for Markdown or JSON; saved ledgers/views retain them. Inspect returned-file terminology/structure findings with validate_returned_file or review_project_view; page all findings under unchanged digests. Diagnostics do not decide intent or grant application authority. Keep this connection separate from a neutral agent's tools. Review before applying; inspect and explicitly recover partial imports.",
    )
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

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def initialize_identity(name: str) -> dict[str, Any]:
        """Operator: explicitly create this workspace's local transport identity once."""
        return identity.initialize(workspace, name)

    @server.tool(annotations={"readOnlyHint": True})
    def recipient_registry() -> dict[str, Any]:
        """Operator: inspect peer fingerprints, lifecycle history and registry digest, never key bytes."""
        return identity.status(workspace)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    def change_recipient(
        peer: str,
        action: str,
        key: str | None = None,
        key_id: str | None = None,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Operator: register, rotate or permanently revoke a peer key; changes require the current registry digest."""
        return identity.change_peer(
            workspace,
            peer,
            action=action,
            key=key,
            key_id=key_id,
            expected_sha256=expected_sha256,
        )

    @server.tool(annotations={"readOnlyHint": True})
    def inspect_delivery(delivery_id: str) -> dict[str, Any]:
        """Operator: inspect a retained pending/completed delivery without retrying it."""
        return identity.delivery_status(workspace, delivery_id)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def seal_file(
        source: str, output: str, peer: str, ttl: int = 86400
    ) -> dict[str, Any]:
        """Operator: encrypt a file for an explicitly provisioned peer using its active key."""
        try:
            return transfer_file(
                workspace, source, output, peer=peer, opening=False, ttl=ttl
            )
        except OperationError as exc:
            return {**exc.report, "ok": False}

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def open_file(
        source: str, output: str, peer: str, recover: bool = False
    ) -> dict[str, Any]:
        """Operator: authenticate and admit a file delivery once, or recover its exact partial operation."""
        try:
            return transfer_file(
                workspace, source, output, peer=peer, opening=True, recover=recover
            )
        except OperationError as exc:
            return {**exc.report, "ok": False}

    @server.tool(annotations={"readOnlyHint": True})
    def cipher_catalog() -> dict[str, Any]:
        """Discover exact classical variants and separate semantic/authenticated operations."""
        return capabilities()

    @server.tool(annotations={"readOnlyHint": True})
    def preview_cipher_transform(
        source: str,
        recipe: str,
        output: str,
        reverse: bool = False,
        recover_metadata: bool = False,
    ) -> dict[str, Any]:
        """Operator: review a private .cipher-recipe.json on bytes; use apply_preview.

        Classical obfuscation is not confidentiality. Decrypt/reverse transport
        before returning source for semantic validation or application execution.
        """
        plan = prepare_transform(
            workspace,
            source,
            recipe,
            output,
            reverse=reverse,
            recover_metadata=recover_metadata,
        )
        return retain(plan, plan.report(), recover_metadata)

    @server.tool(annotations={"readOnlyHint": True})
    def verify_cipher_transform(
        source: str, receipt: str | None = None
    ) -> dict[str, Any]:
        """Verify bytes and exact inverse against the private retained cipher receipt."""
        return verify_transform(workspace, source, receipt=receipt)

    @server.tool(annotations={"readOnlyHint": True})
    def dictionary_status() -> dict[str, Any]:
        """Describe the validated mapping without returning its terms."""
        return {
            "entries": len(mapping),
            "version": mapping.version,
            "sha256": mapping.fingerprint,
        }

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def pack_project_view(
        view_id: str,
        output: str,
        key: str | None = None,
        recipient: str | None = None,
        recipe: str | None = None,
        peer: str | None = None,
        ttl: int = 86400,
    ) -> dict[str, Any]:
        """Operator: pack a fresh neutral view through optional classical transforms and Tessera."""
        try:
            return pack_view(
                workspace,
                view_id,
                output,
                key=key,
                recipient=recipient,
                recipe=recipe,
                peer=peer,
                ttl=ttl,
            )
        except OperationError as exc:
            return {**exc.report, "ok": False}

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def open_project_exchange(
        packet: str,
        output: str,
        key: str | None = None,
        recipient: str | None = None,
        recipe: str | None = None,
        peer: str | None = None,
        recover: bool = False,
    ) -> dict[str, Any]:
        """Operator: authenticate/unpack a new neutral workspace; explicitly recover partial extraction."""
        try:
            return open_exchange(
                workspace,
                packet,
                output,
                key=key,
                recipient=recipient,
                recipe=recipe,
                peer=peer,
                recover=recover,
            )
        except OperationError as exc:
            return {**exc.report, "ok": False}

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def pack_project_return(
        exchange_id: str,
        output: str,
        key: str | None = None,
        recipient: str | None = None,
        recipe: str | None = None,
        peer: str | None = None,
        ttl: int = 86400,
    ) -> dict[str, Any]:
        """Operator: authenticate the receiver's entire edited/new project for source-owner review."""
        try:
            return pack_return(
                workspace,
                exchange_id,
                output,
                key=key,
                recipient=recipient,
                recipe=recipe,
                peer=peer,
                ttl=ttl,
            )
        except OperationError as exc:
            return {**exc.report, "ok": False}

    @server.tool(annotations={"readOnlyHint": True})
    def project_exchange_context(exchange_id: str) -> dict[str, Any]:
        """Return a completed receiver's neutral workspace and ordinary-file agent instructions."""
        return exchange_context(workspace, exchange_id)

    @server.tool(annotations={"readOnlyHint": True})
    def preview_template_fixture(source: str, rules: str) -> dict[str, Any]:
        """Operator: generate neutral records and validate contracts/cases before v3 view export."""
        return preview_fixture(workspace, source, rules)

    @server.tool(annotations={"readOnlyHint": True})
    def validate_template_fixture(
        source: str, contract: str, fixture: str | None = None
    ) -> dict[str, Any]:
        """Check prepared records using the public contract without changing any file."""
        return validate_fixture(workspace, source, contract, fixture=fixture)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def export_dictionary_lexicon(
        output: str, mapping_version: str, dictionary: str | None = None
    ) -> dict[str, Any]:
        """Operator only: export all real mappings as editable concepts in a new .lexicon.json."""
        selected = (
            Dictionary.from_document(parse_json(workspace.read(dictionary)))
            if dictionary
            else mapping
        )
        return export_lexicon(workspace, selected, output, mapping_version)

    @server.tool(annotations={"readOnlyHint": True})
    def check_dictionary_lexicon(source: str) -> dict[str, Any]:
        """Locate schema errors and source/neutral collisions without writing files."""
        return check_lexicon(workspace, source)

    @server.tool(annotations={"readOnlyHint": True})
    def query_dictionary_lexicon(
        source: str,
        query: str = "",
        side: str = "both",
        offset: int = 0,
        limit: int = 50,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Operator only: inspect source/neutral forms; pin lexicon_sha256 for pagination."""
        return query_lexicon(
            workspace,
            source,
            query=query,
            side=side,
            offset=offset,
            limit=limit,
            expected_sha256=expected_sha256,
        )

    @server.tool(annotations={"readOnlyHint": True})
    def preview_lexicon_edit(
        source: str, patch: str, output: str, recover_metadata: bool = False
    ) -> dict[str, Any]:
        """Review explicit concept upserts/removals into a new lexicon; use apply_preview."""
        plan = prepare_edit(
            workspace, source, patch, output, recover_metadata=recover_metadata
        )
        return retain(plan, plan.report(), recover_metadata)

    @server.tool(annotations={"readOnlyHint": True})
    def preview_lexicon_compilation(
        source: str,
        output: str,
        base: str | None = None,
        offset: int = 0,
        limit: int = 100,
        expected_sha256: str | None = None,
        recover_metadata: bool = False,
    ) -> dict[str, Any]:
        """Review exact added/changed/removed mappings and new versioned dictionary.

        Inspect every change page with the same review_sha256. apply_preview publishes
        that complete artifact plus provenance; source-agent MCP never has this tool.
        """
        plan = prepare_compile(
            workspace,
            source,
            output,
            base=base,
            offset=offset,
            limit=limit,
            expected_sha256=expected_sha256,
            recover_metadata=recover_metadata,
        )
        return retain(plan, plan.report(), recover_metadata)

    @server.tool(annotations={"readOnlyHint": True})
    def verify_dictionary_compilation(source: str) -> dict[str, Any]:
        """Reproduce a compiled dictionary from retained provenance; report live input drift."""
        return verify_compilation(workspace, source)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def export_project_view(config: str, output: str) -> dict[str, Any]:
        """Export explicitly selected files into a new neutral development view.

        Originals and reverse mappings remain private in the authoritative project.
        """
        try:
            return export_view(
                workspace, config, output, mapping, private_paths=private_paths
            )
        except OperationError as exc:
            return {**exc.report, "ok": False}
        except FixtureError as exc:
            return exc.report

    @server.tool(annotations={"readOnlyHint": True})
    def review_project_view(
        view_id: str,
        delete_files: list[str] | None = None,
        validation_path: str | None = None,
        validation_offset: int = 0,
        max_findings: int = 100,
        return_packet: str | None = None,
        key: str | None = None,
        recipient: str | None = None,
        recipe: str | None = None,
        peer: str | None = None,
    ) -> dict[str, Any]:
        """Review decoded multi-file changes; missing files need explicit deletion selection."""
        return review_import(
            workspace,
            view_id,
            delete_files=delete_files,
            validation_path=validation_path,
            validation_offset=validation_offset,
            max_findings=max_findings,
            return_packet=return_packet,
            key=key,
            recipient=recipient,
            recipe=recipe,
            peer=peer,
        )

    @server.tool(annotations={"readOnlyHint": True})
    def project_view_status(view_id: str) -> dict[str, Any]:
        """Inspect view/import state and conflicts without changing source."""
        return view_status(workspace, view_id)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    def apply_project_view(
        view_id: str,
        review_sha256: str,
        delete_files: list[str] | None = None,
        recover: bool = False,
        return_packet: str | None = None,
        key: str | None = None,
        recipient: str | None = None,
        recipe: str | None = None,
        peer: str | None = None,
    ) -> dict[str, Any]:
        """Apply exact reviewed source edits or resume a retained import explicitly.

        Backs up originals privately; refuses stale inputs and unreviewed deletions.
        """
        try:
            return apply_import(
                workspace,
                view_id,
                review_sha256,
                delete_files=delete_files,
                recover=recover,
                return_packet=return_packet,
                key=key,
                recipient=recipient,
                recipe=recipe,
                peer=peer,
            )
        except OperationError as exc:
            return {**exc.report, "ok": False}

    @server.tool(annotations={"readOnlyHint": True})
    def scan_file(source: str, max_matches: int = 1000) -> dict[str, Any]:
        """Inspect dictionary coverage with term counts and character locations; no writes.

        Operator tool: findings include original terms. Not part of serve-agent.
        """
        return scan(workspace, source, mapping, max_matches=max_matches)

    @server.tool(annotations={"readOnlyHint": True})
    def preview_file(
        source: str,
        output: str | None = None,
        reverse: bool = False,
        ledger: str | None = None,
        strict: bool = False,
        dictionary: str | None = None,
        document_format: str | None = None,
        json_pointers: list[str] | None = None,
        python_identifiers: list[str] | None = None,
        python_modules: list[str] | None = None,
        python_strings: list[str] | None = None,
        yaml_pointers: list[str] | None = None,
        protect: list[str] | None = None,
        json_values_only: bool = False,
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
            format_rules=format_options(
                document_format,
                json_pointers,
                python_identifiers,
                python_modules,
                python_strings,
                yaml_pointers,
                protect,
                json_values_only,
            ),
        )
        return retain(plan, preview(plan))

    @server.tool(annotations={"readOnlyHint": True})
    def validate_returned_file(
        source: str,
        original: str,
        ledger: str | None = None,
        offset: int = 0,
        max_findings: int = 100,
    ) -> dict[str, Any]:
        """Operator-only terminology/structure diagnostics bound to saved context; never rewrites text or grants apply authority."""
        return validate_return(
            workspace,
            source,
            original,
            ledger=ledger,
            offset=offset,
            max_findings=max_findings,
        )

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
        """Report baseline drift and violations of the operator's structural profile."""
        return audit(workspace)

    @server.tool(annotations={"readOnlyHint": True})
    def check_repository_structure(
        rules: str | None = None,
        offset: int = 0,
        limit: int = 100,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Operator-only required-path, syntax, size and companion diagnostics. Page with the same check_sha256; no baseline or source mutation is required."""
        return check_structure(
            workspace,
            rules,
            offset=offset,
            limit=limit,
            expected_sha256=expected_sha256,
        )

    return server
