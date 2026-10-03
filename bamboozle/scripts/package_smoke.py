"""Exercise an installed wheel from a directory outside the source package."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

import bamboozle
from bamboozle.dictionary import Dictionary
from bamboozle.utils import sha256
from workshop import examples, plans, process
from workshop import operations as ops
from workshop.pipeline import run_script
from workshop.scrape.alias_pass import Taxonomy


def smoke_capture() -> list[dict]:
    """Exercise noisy external scripts using only the installed adapter."""
    scrape = Path("capture-fixture")
    scrape.mkdir()
    script = (
        "import sys, threading, time\nfrom pathlib import Path\n"
        "def emit(stream, byte, ending):\n"
        "    for _ in range(64):\n"
        "        stream.write(byte * 65536)\n"
        "    stream.write(ending)\n    stream.flush()\n"
        "Path('partial.bin').write_bytes(b'retained synthetic output')\n"
        "threads = [threading.Thread(target=emit, args=(sys.stdout.buffer, b'O', b'out-end')),\n"
        "           threading.Thread(target=emit, args=(sys.stderr.buffer, b'E', b'err-end'))]\n"
        "for thread in threads: thread.start()\n"
        "for thread in threads: thread.join()\n"
        "if sys.argv[-1] == 'timeout': time.sleep(60)\n"
        "raise SystemExit(9 if sys.argv[-1] == 'failure' else 0)\n"
    )
    (scrape / "promote.py").write_text(script, encoding="utf-8")
    results = []
    for mode, status, code in (
        ("success", "completed", 0),
        ("failure", "failed", 9),
        ("timeout", "timed_out", 124),
    ):
        report = run_script(scrape, Path(sys.executable), "promote.py", mode, timeout=5)
        if report["status"] != status or report["returncode"] != code:
            raise RuntimeError(f"Installed capture outcome mismatch: {report}")
        for name, byte, ending, limit in (
            ("stdout", "O", "out-end", 8000),
            ("stderr", "E", "err-end", 4000),
        ):
            stream = report["capture"][name]
            if (
                report[name] != byte * (limit - len(ending)) + ending
                or not report[f"{name}_truncated"]
                or stream["bytes_read"] != 64 * 65536 + len(ending)
                or stream["bytes_retained"] != limit
            ):
                raise RuntimeError(f"Installed {name} capture mismatch: {stream}")
        if not report["capture"]["complete"] or not report["child_exited"]:
            raise RuntimeError(
                "Installed capture did not finish draining/reap the child"
            )
        if (scrape / "partial.bin").read_bytes() != b"retained synthetic output":
            raise RuntimeError("Installed adapter changed partial publication evidence")
        results.append(
            {
                "mode": mode,
                "status": status,
                "returncode": code,
                "capture": report["capture"],
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args()
    package_file = Path(bamboozle.__file__).resolve()
    if not package_file.is_relative_to(args.prefix.resolve()):
        raise RuntimeError(
            f"Smoke test loaded a source checkout instead of the wheel: {package_file}"
        )
    if not Path(process.__file__).resolve().is_relative_to(args.prefix.resolve()):
        raise RuntimeError("Capture module was not loaded from the installed wheel")
    if not Path(plans.__file__).resolve().is_relative_to(args.prefix.resolve()):
        raise RuntimeError("Prepared plans were not loaded from the installed wheel")
    mapping = Dictionary.load()
    if len(mapping) != 110 or not Taxonomy.load().canon_cat:
        raise RuntimeError("Packaged dictionary or taxonomy resource is missing")
    Path("mapping.json").write_text('{"alpha":"beta"}', encoding="utf-8")
    original = b"\xef\xbb\xbfAlpha beta\r\n"
    Path("source.txt").write_bytes(original)

    def run(*arguments: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "bamboozle", *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(result.stdout)

    run("validate")
    run("encode", "source.txt", "--dict", "mapping.json")
    run("verify", "source_safe.txt")
    run("decode", "source_safe.txt")
    if Path("source_safe_ready.txt").read_bytes() != original:
        raise RuntimeError("Installed translation did not preserve original bytes")
    run("keygen", "--key", "agent.key")
    run(
        "seal",
        "source.txt",
        "--key",
        "agent.key",
        "--recipient",
        "agent-a",
        "-o",
        "packet.json",
    )
    run(
        "open",
        "packet.json",
        "--key",
        "agent.key",
        "--recipient",
        "agent-a",
        "-o",
        "opened.txt",
    )
    if Path("opened.txt").read_bytes() != original:
        raise RuntimeError("Installed Tessera delivery changed the payload")
    run("baseline")
    run("audit")
    workshop = Path("workshop-fixture")
    run("workshop-init", "--workshop", str(workshop))
    scrape = workshop / "scrape"
    readiness = run("workshop-check", "--workshop", str(workshop))
    if not readiness["ok"] or readiness["deployment_qualified"]:
        raise RuntimeError("Installed pipeline readiness contract failed")
    candidate = examples.candidate(workshop, "ExampleA", ["red hair"])
    for script, arguments in [
        ("promote.py", ["--candidate", candidate, "--dry-run"]),
        ("promote.py", ["--candidate", candidate]),
        ("audit_matrix.py", ["--apply"]),
        ("promote.py", ["--quarantine", "ExampleA", "--reason", "installed fixture"]),
    ]:
        result = run_script(scrape, Path(sys.executable), script, *arguments)
        if result["returncode"] != 0:
            raise RuntimeError(f"Installed native pipeline failed: {result}")

    # Leave a real incomplete journal for the installed workshop stdio server.
    examples.candidate(workshop, "ExampleB", ["red hair"])
    prepared = plans.prepare_promotion(workshop, "candidates/ExampleB.json")
    writer = ops.atomic_write

    def fail_ledger(path: Path, data: bytes, **kwargs) -> None:
        if path == workshop.absolute() / ops.LEDGER:
            raise OSError("Installed reader qualification fault")
        writer(path, data, **kwargs)

    operation_id = ""
    with patch.object(ops, "atomic_write", fail_ledger):
        try:
            plans.apply_plan(
                workshop,
                prepared["operation_id"],
                prepared["plan_sha256"],
                dry_run=False,
            )
        except ops.PendingOperation as exc:
            operation_id = exc.operation_id
    if operation_id != prepared["operation_id"]:
        raise RuntimeError("Installed failure injection did not leave a journal")

    async def smoke_mcp() -> None:
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "bamboozle", "--root", str(Path.cwd()), "serve"],
            cwd=str(Path.cwd()),
        )
        async with Client(transport) as client:
            result = await client.call_tool("dictionary_status", {})
            content = result.content[0]
            if (
                result.is_error
                or content.type != "text"
                or json.loads(content.text)["entries"] != 110
            ):
                raise RuntimeError("Installed MCP tool call failed")

    asyncio.run(asyncio.wait_for(smoke_mcp(), timeout=30))

    async def smoke_workshop() -> None:
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "workshop.mcp.workshop_mcp"],
            cwd=str(Path.cwd()),
            env={"VIGILES_WORKSHOP_ROOT": str(workshop.absolute())},
        )
        async with Client(transport) as client:
            failed = await client.call_tool("query_manifest", {}, raise_on_error=False)
            if not failed.is_error:
                raise RuntimeError("Installed workshop exposed incomplete publication")
            status = await client.call_tool("pipeline_status", {"rebuild_index": True})
            if "blocked_pending_recovery" not in str(status.content):
                raise RuntimeError("Installed workshop did not block index rebuild")
            pending_inventory = await client.call_tool("list_plans", {})
            pending_content = pending_inventory.content[0]
            if pending_content.type != "text":
                raise RuntimeError("Installed pending inventory did not return text")
            pending_report = json.loads(pending_content.text)
            if pending_report["returncode"] != 0 or pending_report["stdout_truncated"]:
                raise RuntimeError("Installed pending inventory failed")
            if (
                json.loads(pending_report["stdout"])["items"][0]["status"]
                != "recovery_required"
            ):
                raise RuntimeError("Installed inventory hid the pending plan")
            recovered = run_script(
                scrape, Path(sys.executable), "promote.py", "--recover", operation_id
            )
            if recovered["returncode"] != 0:
                raise RuntimeError(f"Installed reader recovery failed: {recovered}")
            if (
                json.loads(recovered["stdout"])["journal_sha256"]
                != prepared["plan_sha256"]
            ):
                raise RuntimeError("Installed recovery changed the reviewed plan")
            query = await client.call_tool("lora_who", {"trigger": "red hair"})
            content = query.content[0]
            if content.type != "text" or json.loads(content.text)["loras"] != [
                "ExampleB"
            ]:
                raise RuntimeError("Installed workshop did not refresh after recovery")

    asyncio.run(asyncio.wait_for(smoke_workshop(), timeout=30))
    examples.candidate(workshop, "ExampleC", ["neon"])

    async def smoke_plans(module: str) -> dict:
        transport = StdioTransport(
            command=sys.executable,
            args=["-c", f"from {module} import mcp; mcp.run()"],
            cwd=str(Path.cwd()),
            env={"VIGILES_WORKSHOP_ROOT": str(workshop.absolute())},
        )
        async with Client(transport) as client:

            async def call(name: str, arguments: dict, code: int = 0) -> dict:
                result = await client.call_tool(name, arguments)
                content = result.content[0]
                if content.type != "text":
                    raise RuntimeError("Installed plan tool did not return JSON text")
                report = json.loads(content.text)
                if report["returncode"] != code or report["stdout_truncated"]:
                    raise RuntimeError(f"Installed plan tool failed: {report}")
                return json.loads(report["stdout"])

            stale = await call(
                "prepare_promotion", {"candidate": "candidates/ExampleC.json"}
            )
            review = await call(
                "prepare_promotion", {"candidate": "candidates/ExampleC.json"}
            )
            identity = {
                "plan_id": review["operation_id"],
                "plan_sha256": review["plan_sha256"],
            }
            inspected = await call("inspect_plan", {"plan_id": identity["plan_id"]})
            if (
                not inspected["applicable"]
                or inspected["plan_sha256"] != identity["plan_sha256"]
            ):
                raise RuntimeError("Installed plan inspection mismatch")
            preview = await call("apply_plan", identity)
            if preview["status"] != "plan_preview":
                raise RuntimeError("Installed plan tool default is not preview")
            saved = await call("apply_plan", {**identity, "dry_run": False})
            if saved["journal_sha256"] != identity["plan_sha256"]:
                raise RuntimeError("Installed application changed the reviewed plan")
            replay = await call("apply_plan", {**identity, "dry_run": False}, code=1)
            if "applied" not in replay["error"]:
                raise RuntimeError("Installed application did not reject replay")
            quarantine = await call(
                "prepare_quarantine",
                {"name": "ExampleC", "reason": "installed fixture"},
            )
            removed = await call(
                "apply_plan",
                {
                    "plan_id": quarantine["operation_id"],
                    "plan_sha256": quarantine["plan_sha256"],
                    "dry_run": False,
                },
            )
            if removed["journal_sha256"] != quarantine["plan_sha256"]:
                raise RuntimeError("Installed quarantine changed the reviewed plan")
            unused = await call(
                "prepare_quarantine",
                {"name": "ExampleB", "reason": "cancelled fixture"},
            )
            cancelled = await call(
                "cancel_plan",
                {
                    "plan_id": unused["operation_id"],
                    "plan_sha256": unused["plan_sha256"],
                },
            )
            if (
                cancelled["status"] != "cancelled"
                or not (workshop / "prod/model-ExampleB.bin").exists()
            ):
                raise RuntimeError("Installed cancellation changed production assets")
            retained = await call(
                "prepare_quarantine",
                {"name": "ExampleB", "reason": "inventory fixture"},
            )
            (scrape / "prepared/broken-fixture.tmp").write_bytes(
                b"retained incomplete fixture"
            )

            def inventory_files() -> dict:
                return {
                    str(path.relative_to(workshop)): sha256(path.read_bytes())
                    for path in workshop.rglob("*")
                    if path.is_file()
                }

            before_inventory = inventory_files()
            rows = []
            cursor = None
            page_count = 0
            while True:
                page = await call("list_plans", {"limit": 2, "cursor": cursor})
                if page["asset_hashes_checked"] or page["apply_preflight"]:
                    raise RuntimeError("Inventory incorrectly claimed apply readiness")
                rows.extend(page["items"])
                page_count += 1
                cursor = page["next_cursor"]
                if cursor is None:
                    break
                if page_count > 20:
                    raise RuntimeError("Inventory paging did not finish")
            if inventory_files() != before_inventory:
                raise RuntimeError("Installed inventory mutated workshop files")
            observed = {row.get("operation_id"): row["status"] for row in rows}
            expected = {
                stale["operation_id"]: "stale",
                review["operation_id"]: "applied",
                unused["operation_id"]: "cancelled",
                retained["operation_id"]: "prepared",
            }
            if (
                any(observed.get(key) != value for key, value in expected.items())
                or "corrupt" not in observed.values()
            ):
                raise RuntimeError("Installed inventory states are incomplete")
            if len({row["file"] for row in rows}) != len(rows):
                raise RuntimeError("Installed inventory repeated a record")
            return {
                "module": module,
                "status": "passed",
                "promotion": saved,
                "quarantine": removed,
                "cancelled": cancelled,
                "inventory": {
                    "pages": page_count,
                    "records": len(rows),
                    "states": sorted(set(observed.values())),
                    "read_only": True,
                },
            }

    plan_results = [
        asyncio.run(asyncio.wait_for(smoke_plans(module), timeout=60))
        for module in (
            "workshop.mcp.workshop_mcp",
            "workshop.scrape.bamb5",
        )
    ]
    capture_results = smoke_capture()
    print(
        json.dumps(
            {
                "status": "passed",
                "version": bamboozle.__version__,
                "package_file": str(package_file),
                "capture_results": capture_results,
                "plan_results": plan_results,
                "reviewed_plan_recovery": {
                    "operation_id": operation_id,
                    "plan_sha256": prepared["plan_sha256"],
                },
                "checks": [
                    "bundled_resources",
                    "cli_roundtrip",
                    "ledger_verify",
                    "tessera_roundtrip",
                    "baseline_audit",
                    "mcp_stdio_tool_call",
                    "workshop_readiness_without_execution",
                    "native_promotion_audit_quarantine",
                    "workshop_stdio_pending_read_rejection_and_recovery",
                    "bounded_capture_success_failure_timeout",
                    "both_workshop_stdio_reviewed_plan_prepare_inspect_apply_replay_cancel",
                    "installed_reviewed_plan_fault_recovery",
                    "installed_inventory_pending_recovery_and_both_stdio_paged_states",
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
