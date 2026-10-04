"""Agent-facing Semantic Cipher Middleware: encoded reads and decoded writes."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from bamboozle.agent_checks import AgentChecks
from bamboozle.agent_development import AgentDevelopment
from bamboozle.agent_files import AgentFiles
from bamboozle.agent_policy import AgentPolicy
from bamboozle.dictionary import PROFILES, Dictionary
from bamboozle.service import OperationError
from bamboozle.workspace import Workspace


def register_tools(server: FastMCP, files: AgentFiles) -> None:
    development = AgentDevelopment(files)

    @server.tool(annotations={"readOnlyHint": True})
    def list_safe_files(
        directory: str = ".",
        offset: int = 0,
        limit: int = 100,
        expected_snapshot: str | None = None,
    ) -> dict[str, Any]:
        """Discover source paths within policy, excluding private metadata and links.

        Results are recursive and paginated. Use next_offset and snapshot_sha256
        as expected_snapshot on subsequent pages. Narrow directory on scan limits.
        generated_directories describes neutral selections for new modules.
        """
        return development.list_files(directory, offset, limit, expected_snapshot)

    @server.tool(annotations={"readOnlyHint": True})
    def search_safe_files(
        query: str,
        directory: str = ".",
        offset: int = 0,
        limit: int = 50,
        expected_snapshot: str | None = None,
    ) -> dict[str, Any]:
        """Find an exact case-sensitive, single-line literal in neutral source.

        Search happens after encoding. Line/column coordinates are 1-based Unicode
        characters; end_column is exclusive. Matches include the three edit hashes.
        An unavailable file makes complete false; it is never a negative match.
        Page with next_offset and expected_snapshot; narrow scope on scan limits.
        """
        return development.search(query, directory, offset, limit, expected_snapshot)

    @server.tool(annotations={"readOnlyHint": True})
    def read_safe_file(
        file_path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        """Read source through the semantic encoder. Returns neutral content, never raw source.

        Preserve literal/case/prefix-escape markers and the translated vocabulary.
        Protocol 2 quotes raw marker examples; quote_prefixes handles new raw snippets.
        Keep source_sha256 and context_sha256 for an existing-file write after reconnecting.
        Optional line ranges are inclusive; hashes always describe the full file.
        Use edit_safe_file for partial reads, never overwrite a file with an excerpt.
        document_rules lists neutral selections; inverse mappings remain private.
        """
        return development.read(file_path, start_line, end_line)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    def edit_safe_file(
        file_path: str,
        edits: list[dict[str, Any]],
        expected_sha256: str,
        expected_context_sha256: str,
        expected_neutral_sha256: str,
    ) -> dict[str, Any]:
        """Apply exact neutral replacements, decode and save through normal write history.

        Supply all three hashes from read/search. Each edit contains old_text and
        new_text, with optional 1-based occurrence if old_text repeats. All edits
        reference the SAME original neutral snapshot, not prior replacements.
        Overlap, ambiguity, changed context and invalid syntax are refused before
        source publication. Unedited bytes and line endings are preserved.
        Use write_safe_file for new files or deliberate full-file replacements.
        """
        try:
            return development.edit(
                file_path,
                edits,
                expected_sha256,
                expected_context_sha256,
                expected_neutral_sha256,
            )
        except OperationError as exc:
            return {**exc.report, "ok": False}

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    def write_safe_file(
        file_path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_context_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Decode neutral agent output into domain source and save it locally.

        For existing files, first use read_safe_file; stale changes are refused.
        New files need no original counterpart. Prior source is backed up privately.
        With an operator policy, reconnecting requires expected_context_sha256 too.
        New package directories are created after translation and stale-input checks.
        The response contains publication status and hashes, never decoded source.
        """
        try:
            return files.write(
                file_path, content, expected_sha256, expected_context_sha256
            )
        except OperationError as exc:
            return {**exc.report, "ok": False}


def create_server(
    root: Path,
    dictionary: Dictionary | None = None,
    *,
    private_paths: tuple[Path, ...] = (),
    policy: str | None = None,
    checks: str | None = None,
) -> FastMCP:
    mapping = dictionary if dictionary is not None else Dictionary.load()
    workspace = Workspace(root)
    selected_policy = AgentPolicy(workspace, policy) if policy is not None else None
    server = FastMCP(
        "bamboozle_source_agent",
        instructions=(
            "You work through the Bamboozle semantic cipher. Discover files with list_safe_files, "
            "search the neutral vocabulary with search_safe_files, and read full files or line ranges with read_safe_file. "
            "Apply targeted replacements with edit_safe_file using all three read/search hashes. Never submit a read excerpt as a whole-file write. "
            "The read response's document_rules and listing's generated_directories contain neutral selections only. "
            "Read project source with read_safe_file; "
            "it encodes domain terminology into the working vocabulary. Submit edits and newly generated "
            "source through write_safe_file; it decodes that vocabulary locally. Preserve __SCM_LIT_...__ and __SCM_CASE_...__ "
            "markers, including protocol-2 prefix escapes, and do not rename mapped terms. "
            "Use bamboozle.markers.quote_prefixes only on newly authored raw marker snippets, never on already encoded content. "
            "Never request private dictionaries, keys or history. "
            "When an operator supplies --policy, these same tools use its syntax-aware Python, Markdown, JSON, YAML or text rules. "
            "YAML selections include every alias value path affected by an anchored scalar; preserve aliases and required string fields. "
            "Without a policy they use whole-term text translation. After reconnecting, configured existing-file writes require both "
            "source_sha256 and context_sha256 from the read response. Never invent or change private selection rules. "
            "For project-wide fixture substitution and reviewed multi-file publication use a neutral view. "
            + (
                "Configured public scope: " + str(selected_policy.public_scope())
                if selected_policy
                else ""
            )
        ),
    )
    files = AgentFiles(
        workspace, mapping, private_paths=private_paths, policy=selected_policy
    )
    register_tools(server, files)
    if checks is not None:
        check_service = AgentChecks(files, checks)

        @server.tool(annotations={"readOnlyHint": True})
        def list_safe_checks() -> dict[str, Any]:
            """List operator-configured Python checks and current neutral snapshot hashes.

            Commands, scope and limits are fixed by the operator. Checks run in a
            fresh neutral copy using the server's interpreter. This is process
            lifecycle management, not an OS filesystem/network sandbox.
            """
            return check_service.catalog()

        @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
        def run_safe_check(check_id: str, expected_snapshot: str) -> dict[str, Any]:
            """Execute one named check against its listed neutral source snapshot.

            List again after source edits. No arbitrary commands or extra arguments
            are accepted. Inspect stdout/stderr, exit status, capture/cleanup flags
            and source_current. Results and copies are retained privately by run_id;
            check-generated files are never imported into source automatically.
            """
            return check_service.run(check_id, expected_snapshot)

        @server.tool(annotations={"readOnlyHint": True})
        def inspect_safe_check(run_id: str) -> dict[str, Any]:
            """Read a retained check result after reconnecting, without running it again.

            matches_current_source distinguishes historical results from current
            code. Incomplete records never count as successful execution.
            """
            return check_service.inspect(run_id)

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Semantic cipher file tools for coding agents"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--policy",
        help="Private .agent-policy.json selecting document rules for direct agent I/O",
    )
    parser.add_argument(
        "--checks", help="Private .agent-checks.json defining named application checks"
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--dict", dest="dictionary", type=Path)
    selection.add_argument("--profile", choices=PROFILES, default="default")
    args = parser.parse_args(argv)
    workspace = Workspace(args.root)
    private = (workspace.resolve(args.dictionary),) if args.dictionary else ()
    mapping = Dictionary.load(private[0] if private else None, profile=args.profile)
    create_server(
        workspace.root,
        mapping,
        private_paths=private,
        policy=args.policy,
        checks=args.checks,
    ).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
