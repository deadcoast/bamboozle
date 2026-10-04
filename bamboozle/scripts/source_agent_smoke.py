"""Discover, read, edit and extend a complete application through source-agent MCP."""

from __future__ import annotations

import argparse
import asyncio
import json
import runpy
import subprocess
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from bamboozle import (
    agent_checks,
    agent_development,
    agent_files,
    agent_policy,
    check_process,
)
from bamboozle.agent_checks import CHECK_TOOLS
from bamboozle.agent_development import AGENT_TOOLS
from bamboozle.utils import json_bytes, sha256


def run(workspace: Path | None = None) -> dict:
    root = (workspace or Path("source-agent-workflow")).resolve()
    source, neutral = root / "source", root / "neutral"
    source.mkdir(parents=True)
    neutral.mkdir()
    scenario = runpy.run_path(str(Path(__file__).with_name("python_scenario.py")))
    originals = scenario["originals"](source)
    rules = scenario["rules"]()
    (source / "mapping.dictionary.json").write_bytes(
        json_bytes(
            {
                "task": "stage",
                "build": "assemble",
                "run": "execute",
                "weight": "effort",
                "total": "sum_cost",
                "path": "location",
            }
        )
    )
    (source / "source.agent-policy.json").write_bytes(
        json_bytes(
            {
                "schema_version": 2,
                "files": {
                    name: {"policy": "translate", "document": rules}
                    if name.endswith(".py")
                    else {"policy": "copy"}
                    for name in originals
                },
                "new_files": {".": {"policy": "translate", "document": rules}},
            }
        )
    )
    (source / "source.agent-checks.json").write_bytes(
        json_bytes(
            {
                "schema_version": 1,
                "checks": {
                    name: {
                        "python": command,
                        "directory": ".",
                        "timeout_seconds": 20,
                        "output_bytes": 8192,
                    }
                    for name, command in {
                        "application": [
                            "-m",
                            "pipeline.main",
                            "workload.json",
                            "--capacity",
                            "2",
                        ],
                        "workloads": ["bench.py"],
                        "generated-package": ["-m", "checks.nested.check"],
                    }.items()
                },
            }
        )
    )

    def execute(directory: Path, enhanced: bool) -> dict:
        arguments = [sys.executable, "-m", "pipeline.main", "workload.json"]
        if enhanced:
            arguments += ["--capacity", "2"]
        process = subprocess.run(
            arguments,
            cwd=directory,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        result = json.loads(process.stdout)
        if (
            result["total"] != 10
            or result["critical"] != 8
            or (enhanced and result["makespan"] != 8)
        ):
            raise RuntimeError(
                "Direct agent application failed its dependency/scheduling contract"
            )
        return result

    def benchmark(directory: Path) -> dict:
        process = subprocess.run(
            [sys.executable, "bench.py"],
            cwd=directory,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        result = json.loads(process.stdout)
        if (
            result["scheduler_executions"] != 300
            or result["invalid_workloads"] != 12
            or not result["protected_literals_preserved"]
        ):
            raise RuntimeError(
                "Direct agent application failed workload/error coverage"
            )
        return result

    baseline = execute(source, False)

    def transport() -> StdioTransport:
        return StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "bamboozle",
                "--root",
                str(source),
                "serve-agent",
                "--dict",
                "mapping.dictionary.json",
                "--policy",
                "source.agent-policy.json",
                "--checks",
                "source.agent-checks.json",
            ],
            cwd=str(source),
        )

    async def operate() -> dict:
        reads = {}

        async def call(client: Client, tool: str, args: dict) -> dict:
            result = await client.call_tool(tool, args)
            if (
                result.is_error
                or not isinstance(result.data, dict)
                or result.data.get("ok") is False
            ):
                raise RuntimeError(
                    f"Direct source-agent operation failed: {tool}: {result}"
                )
            return result.data

        async with Client(transport()) as client:
            tools = sorted(tool.name for tool in await client.list_tools())
            if set(tools) != AGENT_TOOLS | CHECK_TOOLS:
                raise RuntimeError("Source-agent connection exposed operator tools")
            check_catalog = await call(client, "list_safe_checks", {})
            application = next(
                row
                for row in check_catalog["checks"]
                if row["check_id"] == "application"
            )
            failed_check = await call(
                client,
                "run_safe_check",
                {
                    "check_id": "application",
                    "expected_snapshot": application["snapshot_sha256"],
                },
            )
            if (
                failed_check["status"] != "failed"
                or "--capacity" not in failed_check["stderr"]
            ):
                raise RuntimeError(
                    "Baseline application did not expose its missing capacity CLI through the agent check"
                )
            listing = await call(client, "list_safe_files", {"limit": 2})
            discovered = list(listing["results"])
            while listing["next_offset"] is not None:
                listing = await call(
                    client,
                    "list_safe_files",
                    {
                        "offset": listing["next_offset"],
                        "limit": 2,
                        "expected_snapshot": listing["snapshot_sha256"],
                    },
                )
                discovered.extend(listing["results"])
            if {row["file_path"] for row in discovered} != set(originals):
                raise RuntimeError(
                    "Agent discovery omitted source or exposed private files"
                )
            search = await call(client, "search_safe_files", {"query": "def execute("})
            if not search["complete"] or search["total"] != 1:
                raise RuntimeError(
                    "Agent could not locate the translated application entrypoint"
                )
            located = search["results"][0]
            excerpt = await call(
                client,
                "read_safe_file",
                {
                    "file_path": located["file_path"],
                    "start_line": located["line"],
                    "end_line": located["line"] + 1,
                },
            )
            if not excerpt["partial"] or "def execute(rows):" not in excerpt["content"]:
                raise RuntimeError(
                    "Agent ranged read did not expose the neutral entrypoint"
                )
            for row in discovered:
                name = row["file_path"]
                response = await call(client, "read_safe_file", {"file_path": name})
                reads[name] = response
                path = neutral / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(response["content"].encode("utf-8"))
        if execute(neutral, False) != baseline:
            raise RuntimeError(
                "Source-agent reads did not produce a working neutral application"
            )
        scenario["agent_edit"](neutral)
        neutral_run, neutral_bench = execute(neutral, True), benchmark(neutral)
        # Reconnect between reads and writes: both source and translation context
        # must remain bound, including selected strings and private Python spellings.
        saved = {}
        async with Client(transport()) as client:
            for path in sorted(neutral.rglob("*.py")):
                name = path.relative_to(neutral).as_posix()
                content = path.read_bytes().decode("utf-8")
                prior = reads.get(name)
                if prior is not None and content == prior["content"]:
                    continue
                args = {"file_path": name, "content": content}
                if prior is not None:
                    args.update(
                        expected_sha256=prior["source_sha256"],
                        expected_context_sha256=prior["context_sha256"],
                    )
                if name == located["file_path"]:
                    if prior is None:
                        raise RuntimeError(
                            "Located entrypoint was not read before editing"
                        )
                    # Send only four targeted changes through the normal decoder,
                    # including CLI wiring; all anchors refer to the original read.
                    edits = [
                        {
                            "old_text": "from .graph import assemble",
                            "new_text": "from .timeline import simulate",
                        },
                        {
                            "old_text": "def execute(rows):\n    return assemble(rows)",
                            "new_text": "def execute(rows, capacity=2):\n    return simulate(rows, capacity)",
                        },
                        {
                            "old_text": 'parser.add_argument("input")',
                            "new_text": 'parser.add_argument("input")\n    parser.add_argument("--capacity", type=int, default=2)',
                        },
                        {
                            "old_text": 'encoding="utf-8")))',
                            "new_text": 'encoding="utf-8")), args.capacity)',
                        },
                    ]
                    saved[name] = await call(
                        client,
                        "edit_safe_file",
                        {
                            "file_path": name,
                            "edits": edits,
                            "expected_sha256": prior["source_sha256"],
                            "expected_context_sha256": prior["context_sha256"],
                            "expected_neutral_sha256": prior["neutral_sha256"],
                        },
                    )
                    if (await call(client, "read_safe_file", {"file_path": name}))[
                        "content"
                    ] != content:
                        raise RuntimeError(
                            "Targeted agent edit differs from tested neutral application"
                        )
                    stale = await client.call_tool(
                        "edit_safe_file",
                        {
                            "file_path": name,
                            "edits": edits,
                            "expected_sha256": prior["source_sha256"],
                            "expected_context_sha256": prior["context_sha256"],
                            "expected_neutral_sha256": prior["neutral_sha256"],
                        },
                        raise_on_error=False,
                    )
                    if not stale.is_error:
                        raise RuntimeError(
                            "Agent accepted stale edits after a completed publication"
                        )
                else:
                    saved[name] = await call(client, "write_safe_file", args)
            # A real newly generated package, using the translated project API.
            generated = (
                "import json\nfrom pathlib import Path\nfrom pipeline.main import execute\n\n"
                'rows = json.loads(Path("workload.json").read_text(encoding="utf-8"))\n'
                'result = execute(rows, 2)\nassert result["makespan"] == 8\n'
            )
            saved["checks/nested/check.py"] = await call(
                client,
                "write_safe_file",
                {"file_path": "checks/nested/check.py", "content": generated},
            )
            stale_check = await client.call_tool(
                "run_safe_check",
                {
                    "check_id": "application",
                    "expected_snapshot": application["snapshot_sha256"],
                },
                raise_on_error=False,
            )
            if not stale_check.is_error:
                raise RuntimeError(
                    "Agent check accepted a snapshot predating its source edits"
                )
            historical_check = await call(
                client, "inspect_safe_check", {"run_id": failed_check["run_id"]}
            )
            if (
                historical_check["status"] != "failed"
                or historical_check["matches_current_source"]
            ):
                raise RuntimeError(
                    "Failed baseline check lost its history/source-drift distinction"
                )
            check_catalog = await call(client, "list_safe_checks", {})
            passed_checks = {}
            for selected in check_catalog["checks"]:
                result = await call(
                    client,
                    "run_safe_check",
                    {
                        "check_id": selected["check_id"],
                        "expected_snapshot": selected["snapshot_sha256"],
                    },
                )
                if (
                    result["status"] != "passed"
                    or not result["source_current"]
                    or not result["cleanup"]["complete"]
                ):
                    raise RuntimeError(f"Agent application check failed: {result}")
                passed_checks[selected["check_id"]] = result
            if json.loads(passed_checks["workloads"]["stdout"]) != neutral_bench:
                raise RuntimeError(
                    "Agent-run workload checks differ from independently executed application"
                )
            if json.loads(passed_checks["application"]["stdout"])["makespan"] != 8:
                raise RuntimeError(
                    "Agent-run application returned the wrong capacity schedule"
                )
        return {
            "agent_tools": tools,
            "writes": saved,
            "neutral_execution": neutral_run,
            "neutral_benchmark": neutral_bench,
            "reconnected_before_writes": True,
            "discovered_files": [row["file_path"] for row in discovered],
            "entrypoint_search": search,
            "entrypoint_excerpt": excerpt,
            "stale_patch_refused": True,
            "failed_baseline_check": failed_check,
            "historical_check_after_reconnect": historical_check,
            "agent_application_checks": passed_checks,
            "stale_check_refused": True,
        }

    flow = asyncio.run(asyncio.wait_for(operate(), timeout=150))
    restored_run, restored_bench = execute(source, True), benchmark(source)
    if (
        flow["neutral_execution"] != restored_run
        or flow["neutral_benchmark"] != restored_bench
    ):
        raise RuntimeError("Direct source-agent return changed application behavior")
    for name in ("pipeline/graph.py", "pipeline/model.py"):
        if (source / name).read_bytes() != originals[name]:
            raise RuntimeError(
                "Direct source-agent return changed unedited original bytes"
            )
    subprocess.run(
        [sys.executable, "-m", "checks.nested.check"],
        cwd=source,
        check=True,
        capture_output=True,
        timeout=10,
    )
    return {
        "source_root": str(source),
        "neutral_root": str(neutral),
        **flow,
        "restored_execution": restored_run,
        "restored_benchmark": restored_bench,
        "original_sha256": {name: sha256(raw) for name, raw in originals.items()},
        "agent_files_module": str(Path(agent_files.__file__).resolve()),
        "agent_policy_module": str(Path(agent_policy.__file__).resolve()),
        "agent_development_module": str(Path(agent_development.__file__).resolve()),
        "agent_checks_module": str(Path(agent_checks.__file__).resolve()),
        "check_process_module": str(Path(check_process.__file__).resolve()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    print(json.dumps(run(parser.parse_args().workspace), indent=2))
