"""Agent-facing Semantic Cipher Middleware: encoded reads and decoded writes."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from bamboozle.agent_files import AgentFiles
from bamboozle.dictionary import PROFILES, Dictionary
from bamboozle.service import OperationError
from bamboozle.workspace import Workspace


def register_tools(server: FastMCP, files: AgentFiles) -> None:
    @server.tool(annotations={"readOnlyHint": True})
    def read_safe_file(file_path: str) -> dict[str, Any]:
        """Read source through the semantic encoder. Returns neutral content, never raw source.

        Use this for source reads; preserve literal/case markers and the translated vocabulary.
        Keep source_sha256 for an existing-file write after a server reconnect.
        """
        return files.read(file_path)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    def write_safe_file(
        file_path: str,
        content: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Decode neutral agent output into domain source and save it locally.

        For existing files, first use read_safe_file; stale changes are refused.
        New files need no original counterpart. Prior source is backed up privately.
        The response contains publication status and hashes, never decoded source.
        """
        try:
            return files.write(file_path, content, expected_sha256)
        except OperationError as exc:
            return {**exc.report, "ok": False}


def create_server(
    root: Path,
    dictionary: Dictionary | None = None,
    *,
    private_paths: tuple[Path, ...] = (),
) -> FastMCP:
    mapping = dictionary if dictionary is not None else Dictionary.load()
    server = FastMCP(
        "semantic_cipher_middleware",
        instructions=(
            "You work through the Bamboozle semantic cipher. Read project source with read_safe_file; "
            "it encodes domain terminology into the working vocabulary. Submit edits and newly generated "
            "source through write_safe_file; it decodes that vocabulary locally. Preserve __SCM_LIT_...__ and __SCM_CASE_...__ "
            "markers and do not rename mapped terms. Never request private dictionaries, keys or history."
        ),
    )
    register_tools(
        server, AgentFiles(Workspace(root), mapping, private_paths=private_paths)
    )
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Semantic cipher file tools for coding agents"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--dict", dest="dictionary", type=Path)
    selection.add_argument("--profile", choices=PROFILES, default="default")
    args = parser.parse_args(argv)
    workspace = Workspace(args.root)
    private = (workspace.resolve(args.dictionary),) if args.dictionary else ()
    mapping = Dictionary.load(private[0] if private else None, profile=args.profile)
    create_server(workspace.root, mapping, private_paths=private).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
