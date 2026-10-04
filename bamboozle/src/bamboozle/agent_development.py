"""Scoped navigation and exact edits over the existing source-agent translator.

Search never scans raw source for a user query. Every match and every patch
coordinate refers to the same neutral document returned by AgentFiles.read.
"""

from __future__ import annotations

import os
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any

from bamboozle.agent_files import AgentFiles
from bamboozle.project_views import document_rules
from bamboozle.utils import json_bytes, sha256

AGENT_TOOLS = frozenset(
    {
        "list_safe_files",
        "search_safe_files",
        "read_safe_file",
        "edit_safe_file",
        "write_safe_file",
    }
)
MAX_ENTRIES = 20000
MAX_FILES = 2000
MAX_SEARCH_BYTES = 64 * 1024 * 1024
MAX_MATCHES = 10000
CACHE_DIRECTORIES = frozenset({".pytest_cache", ".mypy_cache", ".ruff_cache"})


def integer(value: int, name: str, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")


def page(
    records: list[dict[str, Any]],
    identity: Any,
    offset: int,
    limit: int,
    expected_snapshot: str | None,
) -> dict[str, Any]:
    integer(offset, "offset", 0, MAX_MATCHES)
    integer(limit, "limit", 1, 200)
    digest = sha256(json_bytes(identity))
    if expected_snapshot is not None and digest != expected_snapshot:
        raise ValueError("Navigation snapshot changed; restart at offset 0")
    if offset > len(records):
        raise ValueError("offset is beyond the available results")
    end = min(offset + limit, len(records))
    return {
        "results": records[offset:end],
        "total": len(records),
        "offset": offset,
        "next_offset": end if end < len(records) else None,
        "snapshot_sha256": digest,
    }


class AgentDevelopment:
    """One source scope, one codec, and one publication path for all agent tools."""

    def __init__(self, files: AgentFiles):
        self.files = files

    def _scope(self) -> dict[str, Any]:
        policy = self.files.policy
        if policy:
            policy.recheck()
        return {
            "policy_sha256": policy.fingerprint if policy else None,
            "dictionary_sha256": self.files.dictionary.fingerprint,
            "marker_version": self.files.cipher.marker_version,
        }

    def _directory_selected(self, path: Path) -> bool:
        policy = self.files.policy
        if policy is None:
            return True
        name = PurePosixPath(path.relative_to(self.files.workspace.root).as_posix())
        return any(
            PurePosixPath(selected).is_relative_to(name)
            for selected, rule in policy.document["files"].items()
            if rule["policy"] != "exclude"
        ) or any(
            prefix == "."
            or name.is_relative_to(PurePosixPath(prefix))
            or PurePosixPath(prefix).is_relative_to(name)
            for prefix in policy.document["new_files"]
        )

    def _inventory(self, directory: str) -> list[dict[str, Any]]:
        self._scope()
        root = (
            self.files.workspace.root
            if directory == "."
            else self.files._path(directory)
        )
        if not root.is_dir():
            raise ValueError("directory must name an existing source directory")
        if not self._directory_selected(root):
            raise ValueError("Directory is outside the operator's source-agent policy")
        pending, records, visited = [root], [], 0
        while pending:
            current = pending.pop()
            if current != self.files.workspace.root:
                self.files._path(str(current))
            with os.scandir(current) as entries:
                for entry in entries:
                    visited += 1
                    if visited > MAX_ENTRIES:
                        raise ValueError(
                            "Navigation entry limit exceeded; choose a narrower directory"
                        )
                    try:
                        path = self.files._path(entry.path)
                    except ValueError:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if (
                            path.name.casefold() not in CACHE_DIRECTORIES
                            and self._directory_selected(path)
                        ):
                            pending.append(path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    try:
                        action, rules = self.files._selection(path)
                    except ValueError:
                        # A changed policy is an operation failure, never an empty scope.
                        self._scope()
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    records.append(
                        {
                            "file_path": path.relative_to(
                                self.files.workspace.root
                            ).as_posix(),
                            "source_bytes": stat.st_size,
                            "modified_ns": stat.st_mtime_ns,
                            "document_format": "copy"
                            if action == "copy"
                            else rules.format,
                        }
                    )
                    if len(records) > MAX_FILES:
                        raise ValueError(
                            "Navigation file limit exceeded; choose a narrower directory"
                        )
        self._scope()
        return sorted(records, key=lambda row: row["file_path"])

    def list_files(
        self,
        directory: str = ".",
        offset: int = 0,
        limit: int = 100,
        expected_snapshot: str | None = None,
    ) -> dict[str, Any]:
        records = self._inventory(directory)
        scope = self._scope()
        generated = {}
        if self.files.policy:
            for prefix, rule in self.files.policy.document["new_files"].items():
                action = rule["policy"] if isinstance(rule, dict) else rule
                generated[prefix] = self.files.public_rules(
                    action, document_rules(rule)
                )
        return {
            "status": "listed",
            **scope,
            **page(
                records, [scope, directory, records], offset, limit, expected_snapshot
            ),
            "generated_directories": generated,
        }

    def read(
        self, file_path: str, start_line: int = 1, end_line: int | None = None
    ) -> dict[str, Any]:
        integer(start_line, "start_line", 1, self.files.workspace.max_bytes + 1)
        if end_line is not None:
            integer(
                end_line, "end_line", start_line, self.files.workspace.max_bytes + 1
            )
        result = self.files.read(file_path)
        lines = result["content"].splitlines(keepends=True)
        if start_line > max(1, len(lines)):
            raise ValueError("start_line is beyond the neutral document")
        end = min(end_line if end_line is not None else len(lines), len(lines))
        return {
            **result,
            "content": "".join(lines[start_line - 1 : end]),
            "start_line": start_line,
            "end_line": end,
            "total_lines": len(lines),
            "partial": start_line != 1 or end < len(lines),
        }

    def search(
        self,
        query: str,
        directory: str = ".",
        offset: int = 0,
        limit: int = 50,
        expected_snapshot: str | None = None,
    ) -> dict[str, Any]:
        if (
            not isinstance(query, str)
            or not 1 <= len(query) <= 256
            or len(query.splitlines()) != 1
            or "\n" in query
            or "\r" in query
        ):
            raise ValueError(
                "query must be a nonempty single-line literal of at most 256 characters"
            )
        inventory = self._inventory(directory)
        records: list[dict[str, Any]] = []
        observations: list[Any] = []
        consumed = 0
        for item in inventory:
            name = item["file_path"]
            consumed += item["source_bytes"]
            if consumed > MAX_SEARCH_BYTES:
                raise ValueError(
                    "Search byte limit exceeded; choose a narrower directory"
                )
            try:
                result = self.files.read(name, remember=False)
            except (OSError, ValueError, TypeError):
                self._scope()
                # Decoder/OS diagnostics can quote private source terms or paths.
                # Report the public path and an explicit incomplete result instead.
                records.append(
                    {
                        "kind": "unavailable",
                        "file_path": name,
                        "reason": "Cannot produce neutral text; operator inspection required",
                    }
                )
                observations.append(item)
                continue
            hashes = {
                key: result[key]
                for key in ("source_sha256", "context_sha256", "neutral_sha256")
            }
            observations.append({"file_path": name, **hashes})
            text = result["content"]
            consumed += len(text.encode("utf-8"))
            if consumed > MAX_SEARCH_BYTES:
                raise ValueError(
                    "Search byte limit exceeded; choose a narrower directory"
                )
            for line_number, line in enumerate(text.splitlines(), 1):
                position = line.find(query)
                while position >= 0:
                    start = max(0, position - 120)
                    end = min(len(line), position + len(query) + 120)
                    records.append(
                        {
                            "kind": "match",
                            "file_path": name,
                            **hashes,
                            "line": line_number,
                            "column": position + 1,
                            "end_column": position + len(query) + 1,
                            "excerpt": line[start:end],
                            "excerpt_column": start + 1,
                            "excerpt_truncated": start > 0 or end < len(line),
                        }
                    )
                    if len(records) > MAX_MATCHES:
                        raise ValueError(
                            "Search match limit exceeded; narrow the query or directory"
                        )
                    position = line.find(query, position + len(query))
        scope = self._scope()
        return {
            "status": "searched",
            **scope,
            **page(
                records,
                [scope, directory, query, observations, records],
                offset,
                limit,
                expected_snapshot,
            ),
            "query": query,
            "files_examined": len(inventory),
            "complete": all(row["kind"] == "match" for row in records),
        }

    def edit(
        self,
        file_path: str,
        edits: list[dict[str, Any]],
        expected_sha256: str,
        expected_context_sha256: str,
        expected_neutral_sha256: str,
    ) -> dict[str, Any]:
        """Resolve every edit against one neutral snapshot; never cascade replacements."""
        if not isinstance(edits, list) or not 1 <= len(edits) <= 128:
            raise ValueError("edits must contain 1 to 128 exact replacements")
        payload_bytes = 0
        for edit in edits:
            if isinstance(edit, dict):
                for key in ("old_text", "new_text"):
                    if isinstance(edit.get(key), str):
                        payload_bytes += len(edit[key].encode("utf-8"))
            if payload_bytes > 2 * self.files.workspace.max_bytes:
                raise ValueError("Edit payload exceeds the file limit")
        with self.files.lock:
            read = self.files.read(file_path, remember=False)
            for key, expected in (
                ("source_sha256", expected_sha256),
                ("context_sha256", expected_context_sha256),
                ("neutral_sha256", expected_neutral_sha256),
            ):
                if read[key] != expected:
                    raise ValueError(
                        f"{key} changed; read or search again before editing"
                    )
            text = read["content"]
            spans: list[tuple[int, int, str]] = []
            for index, edit in enumerate(edits, 1):
                if (
                    not isinstance(edit, dict)
                    or not {"old_text", "new_text"} <= edit.keys()
                    or set(edit) - {"old_text", "new_text", "occurrence"}
                ):
                    raise ValueError(
                        f"Edit {index} requires old_text/new_text and optional occurrence"
                    )
                old, new = edit["old_text"], edit["new_text"]
                if not isinstance(old, str) or not old or not isinstance(new, str):
                    raise ValueError(
                        f"Edit {index} requires nonempty old_text and string new_text"
                    )
                occurrence = edit.get("occurrence")
                if occurrence is not None:
                    integer(occurrence, "occurrence", 1, self.files.workspace.max_bytes)
                found, count, selected = text.find(old), 0, -1
                while found >= 0:
                    count += 1
                    if occurrence == count or occurrence is None and count == 1:
                        selected = found
                    if occurrence is not None and count == occurrence:
                        break
                    if occurrence is None and count == 2:
                        raise ValueError(
                            f"Edit {index} is ambiguous; supply a 1-based occurrence or more context"
                        )
                    found = text.find(old, found + 1)
                if selected < 0:
                    raise ValueError(
                        f"Edit {index} does not match the neutral snapshot"
                    )
                spans.append((selected, selected + len(old), new))
            spans.sort()
            if any(right[0] < left[1] for left, right in pairwise(spans)):
                raise ValueError("Edits overlap in the neutral snapshot")
            pieces: list[str] = []
            cursor = 0
            for start, end, new in spans:
                pieces.extend((text[cursor:start], new))
                cursor = end
            pieces.append(text[cursor:])
            content = "".join(pieces)
            if content == text:
                raise ValueError("Edits do not change the neutral document")
            # The original decoder validates syntax and retains private originals,
            # context and intent before its existing source publication boundary.
            saved = self.files.write(
                file_path, content, expected_sha256, expected_context_sha256
            )
            return {
                **saved,
                "edits_applied": len(spans),
                "submitted_neutral_sha256": sha256(content.encode("utf-8")),
            }
