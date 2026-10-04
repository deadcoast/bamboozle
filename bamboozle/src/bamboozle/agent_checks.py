"""Operator-selected checks over fresh neutral source, with retained agent results."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from bamboozle.agent_development import AgentDevelopment, integer
from bamboozle.agent_files import AgentFiles
from bamboozle.check_process import run_python
from bamboozle.engine import Translator
from bamboozle.project_views import digest, relative_name, unique_paths
from bamboozle.python_project import validate_project
from bamboozle.utils import (
    atomic_write,
    exclusive_lock,
    json_bytes,
    parse_json,
    sha256,
    utc_now,
)
from bamboozle.workspace import Workspace

CHECK_TOOLS = frozenset({"list_safe_checks", "run_safe_check", "inspect_safe_check"})
HISTORY = Path(".vigiles/cipher/checks")
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024


class AgentChecks:
    def __init__(self, files: AgentFiles, config: str):
        if files.policy is None:
            raise ValueError("Source-agent checks require an explicit --policy")
        self.files = files
        self.path = files.workspace.resolve(config)
        if not self.path.name.casefold().endswith(".agent-checks.json"):
            raise ValueError(
                "Check configuration requires the private .agent-checks.json suffix"
            )
        raw = files.workspace.read(self.path)
        data = parse_json(raw)
        if (
            not isinstance(data, dict)
            or set(data) != {"schema_version", "checks"}
            or type(data["schema_version"]) is not int
            or data["schema_version"] != 1
        ):
            raise ValueError("Check configuration requires schema_version 1 and checks")
        checks = data["checks"]
        if not isinstance(checks, dict) or not 1 <= len(checks) <= 32:
            raise ValueError("Configure 1 to 32 named checks")
        for name, spec in checks.items():
            if (
                not isinstance(name, str)
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None
            ):
                raise ValueError(
                    "Check names require lowercase ASCII letters, digits, hyphens or underscores"
                )
            if (
                not isinstance(spec, dict)
                or not {
                    "python",
                    "directory",
                    "timeout_seconds",
                    "output_bytes",
                }
                <= spec.keys()
                or set(spec)
                - {
                    "python",
                    "directory",
                    "timeout_seconds",
                    "output_bytes",
                    "python_paths",
                }
            ):
                raise ValueError(
                    "Each check requires python, directory, timeout_seconds and output_bytes"
                )
            relative_name(spec["directory"], directory=True)
            paths = spec.get("python_paths", ["."])
            if not isinstance(paths, list) or not 1 <= len(paths) <= 8:
                raise ValueError(
                    "python_paths must contain 1 to 8 relative import directories"
                )
            for path in paths:
                relative_name(path, directory=True)
            if len(set(paths)) != len(paths):
                raise ValueError("python_paths must contain distinct directories")
            spec["python_paths"] = paths
            integer(spec["timeout_seconds"], "timeout_seconds", 1, 300)
            integer(spec["output_bytes"], "output_bytes", 256, 65536)
            arguments = spec["python"]
            if (
                not isinstance(arguments, list)
                or not 1 <= len(arguments) <= 64
                or any(
                    not isinstance(arg, str)
                    or not arg
                    or len(arg) > 2048
                    or any(ord(c) < 32 for c in arg)
                    for arg in arguments
                )
            ):
                raise ValueError(
                    "python must contain 1 to 64 nonempty arguments without control characters"
                )
            if arguments[0] == "-m":
                if (
                    len(arguments) < 2
                    or re.fullmatch(
                        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", arguments[1], flags=re.ASCII
                    )
                    is None
                ):
                    raise ValueError("Python -m checks require a module name")
            elif arguments[0].startswith("-") or not relative_name(
                arguments[0]
            ).endswith(".py"):
                raise ValueError(
                    "Checks execute a relative .py script or -m module; interpreter flags are not accepted"
                )
        self.specs: dict[str, dict[str, Any]] = checks
        self.fingerprint = sha256(raw)
        self.navigation = AgentDevelopment(files)
        files.private_paths.add(self.path)

    def _recheck(self) -> None:
        assert self.files.policy is not None
        self.files.policy.recheck()
        if sha256(self.files.workspace.read(self.path)) != self.fingerprint:
            raise ValueError(
                "Check configuration changed; reconnect before running checks"
            )

    def _snapshot(self, directory: str) -> tuple[dict[str, bytes], dict[str, Any]]:
        self._recheck()
        inventory = self.navigation._inventory(directory)
        names = [row["file_path"] for row in inventory]
        if not names:
            raise ValueError("Check scope contains no source files")
        unique_paths(names)
        content: dict[str, bytes] = {}
        records = {}
        modules = {}
        total = sum(row["source_bytes"] for row in inventory)
        if total > MAX_SNAPSHOT_BYTES:
            raise ValueError(
                "Check source snapshot exceeds 64 MiB; narrow the configured directory"
            )
        for name in names:
            relative_name(name)
            read = self.files.read(name, remember=False)
            raw = read["content"].encode("utf-8")
            total += len(raw)
            if total > MAX_SNAPSHOT_BYTES:
                raise ValueError(
                    "Source plus neutral check snapshot exceeds 64 MiB; narrow the configured directory"
                )
            content[name] = raw
            records[name] = {
                key: read[key]
                for key in ("source_sha256", "context_sha256", "neutral_sha256")
            }
            if read["document_format"] == "python":
                modules[name] = tuple(read["document_rules"]["modules"])
        # Relationships are validated in the same neutral vocabulary the check executes.
        validate_project(content, modules)
        self._recheck()
        if names != [row["file_path"] for row in self.navigation._inventory(directory)]:
            raise ValueError(
                "Source inventory changed during check preparation; list checks again"
            )
        for name, record in records.items():
            if (
                sha256(self.files.workspace.read(self.files._path(name)))
                != record["source_sha256"]
            ):
                raise ValueError(
                    "Source changed during check preparation; list checks again"
                )
        identity = {
            **self.navigation._scope(),
            "checks_sha256": self.fingerprint,
            "directory": directory,
            "files": records,
        }
        return content, identity

    def catalog(self) -> dict[str, Any]:
        scopes = {}
        rows = []
        for name, spec in self.specs.items():
            directory = spec["directory"]
            if directory not in scopes:
                _, scopes[directory] = self._snapshot(directory)
            identity = scopes[directory]
            rows.append(
                {
                    "check_id": name,
                    **spec,
                    "python": list(spec["python"]),
                    "python_paths": list(spec["python_paths"]),
                    "snapshot_sha256": sha256(json_bytes(identity)),
                    "files": len(identity["files"]),
                }
            )
        return {
            "checks": rows,
            "checks_sha256": self.fingerprint,
            "execution": "fresh_neutral_copy",
            "environment": "server_python_with_curated_environment",
            "is_os_sandbox": False,
        }

    def _display(self, text: str, directory: Path, limit: int) -> tuple[str, bool]:
        # Logs are read-only diagnostics, not editable cipher documents. Reuse the
        # encoder's match/case rules without quoting already neutral target terms.
        for path, replacement in (
            (directory / "workspace", "<check-workspace>"),
            (self.files.workspace.root, "<source-workspace>"),
        ):
            text = text.replace(str(path), replacement).replace(
                path.as_posix(), replacement
            )
        scan = Translator(self.files.dictionary, max_output_chars=1024 * 1024).scan(
            text, max_matches=10000
        )
        if not scan["matches_complete"]:
            raise ValueError("Diagnostic match limit exceeded")
        parts: list[str] = []
        position = 0
        for match in scan["matches"]:
            parts.extend((text[position : match["start"]], match["replacement"]))
            position = match["end"]
        parts.append(text[position:])
        text = "".join(parts)
        # Do not send terminal control sequences from check programs to clients.
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        text = "".join(
            c for c in text if c in "\n\r\t" or ord(c) >= 32 and ord(c) != 127
        )
        raw = text.encode("utf-8")
        return raw[-limit:].decode("utf-8", errors="ignore"), len(raw) > limit

    def run(self, check_id: str, expected_snapshot: str) -> dict[str, Any]:
        digest(expected_snapshot)
        if check_id not in self.specs:
            raise ValueError("Unknown check_id; use list_safe_checks")
        spec = self.specs[check_id]
        self._recheck()
        lock = self.files.workspace.resolve(HISTORY / "runner.lock", write=True)
        lock.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(lock):
            content, identity = self._snapshot(spec["directory"])
            if sha256(json_bytes(identity)) != expected_snapshot:
                raise ValueError(
                    "Check snapshot changed; list checks again before running"
                )
            if spec["python"][0] != "-m" and spec["python"][0] not in content:
                raise ValueError("Check script is not in its permitted source snapshot")
            for prefix in spec["python_paths"]:
                if prefix != "." and not any(
                    name.startswith(prefix + "/") for name in content
                ):
                    raise ValueError(
                        "A configured Python import directory is absent from the neutral snapshot"
                    )
            run_id = uuid.uuid4().hex
            directory = self.files.workspace.resolve(
                HISTORY / run_id / "intent.json", write=True
            ).parent
            directory.mkdir()
            intent = {
                "schema_version": 1,
                "kind": "bamboozle.agent-check",
                "run_id": run_id,
                "check_id": check_id,
                "created": utc_now(),
                "snapshot_sha256": expected_snapshot,
                "identity": identity,
                "check": spec,
            }
            atomic_write(directory / "intent.json", json_bytes(intent))
            workspace = directory / "workspace"
            workspace.mkdir()
            result: dict[str, Any] = {
                "run_id": run_id,
                "check_id": check_id,
                "snapshot_sha256": expected_snapshot,
                "status": "preparation_failed",
                "started": False,
            }
            try:
                for name, raw in content.items():
                    path = workspace / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(path, raw)
                _, fresh = self._snapshot(spec["directory"])
                if fresh != identity:
                    raise ValueError(
                        "Source changed before check execution; list checks again"
                    )
                result.update(
                    run_python(
                        spec["python"],
                        workspace,
                        spec["timeout_seconds"],
                        spec["output_bytes"],
                        spec["python_paths"],
                    )
                )
                mutated = []
                neutral_workspace = Workspace(workspace)
                for name, raw in content.items():
                    # Resolve through a new workspace to reject links inserted by a check.
                    try:
                        unchanged = (
                            not (workspace.is_symlink() or workspace.is_junction())
                            and neutral_workspace.read(name) == raw
                        )
                    except (OSError, ValueError):
                        unchanged = False
                    if not unchanged:
                        mutated.append(name)
                result["modified_inputs"] = mutated
                if mutated:
                    result["status"] = "workspace_changed"
                try:
                    _, after = self._snapshot(spec["directory"])
                    current = after == identity
                except (OSError, ValueError, TypeError):
                    current = False
                result["source_current"] = current
                if not current:
                    result["execution_status"] = result["status"]
                    result["status"] = "source_changed"
                for stream in ("stdout", "stderr", "error"):
                    if result.get(stream):
                        try:
                            result[stream], shortened = self._display(
                                result[stream], directory, spec["output_bytes"]
                            )
                            if stream in result["capture"]:
                                result["capture"][stream]["display_truncated"] = (
                                    shortened
                                )
                        except (ValueError, TypeError):
                            result[stream] = (
                                "Diagnostic rendering failed; operator inspection required"
                            )
                            result["status"] = "diagnostics_unavailable"
            except (OSError, ValueError, TypeError) as exc:
                # Original-domain preparation errors never enter the agent channel.
                result.update(
                    status="preparation_failed",
                    error="Check preparation failed; refresh checks or request operator inspection",
                )
                atomic_write(
                    directory / "preparation-error.txt", str(exc).encode("utf-8")
                )
            result["completed"] = utc_now()
            result["intent_sha256"] = sha256(json_bytes(intent))
            raw_result = json_bytes(result)
            atomic_write(directory / "result.json", raw_result)
            atomic_write(
                directory / "result.sha256", sha256(raw_result).encode("ascii")
            )
            return {**result, "result_sha256": sha256(raw_result)}

    def inspect(self, run_id: str) -> dict[str, Any]:
        if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
            raise ValueError("run_id must be the ID returned by run_safe_check")
        directory = self.files.workspace.resolve(
            HISTORY / run_id / "intent.json"
        ).parent
        raw_intent = self.files.workspace.read(directory / "intent.json")
        intent = parse_json(raw_intent)
        if intent["identity"]["dictionary_sha256"] != self.files.dictionary.fingerprint:
            raise ValueError(
                "Check belongs to another translation context; reconnect with its dictionary"
            )
        if (
            not (directory / "result.json").exists()
            or not (directory / "result.sha256").exists()
        ):
            return {
                "run_id": run_id,
                "check_id": intent["check_id"],
                "status": "incomplete",
                "snapshot_sha256": intent["snapshot_sha256"],
            }
        raw = self.files.workspace.read(directory / "result.json")
        if sha256(raw) != self.files.workspace.read(directory / "result.sha256").decode(
            "ascii"
        ):
            raise ValueError(
                "Retained check result digest differs; operator inspection required"
            )
        result = parse_json(raw)
        if result["intent_sha256"] != sha256(raw_intent) or result["run_id"] != run_id:
            raise ValueError(
                "Retained check intent differs; operator inspection required"
            )
        try:
            _, current = self._snapshot(intent["identity"]["directory"])
            current_matches = current == intent["identity"]
        except (OSError, ValueError, TypeError):
            current_matches = False
        return {
            **result,
            "result_sha256": sha256(raw),
            "matches_current_source": current_matches,
        }
