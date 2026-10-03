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
from workshop import examples, process
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
    writer = ops.atomic_write

    def fail_ledger(path: Path, data: bytes, **kwargs) -> None:
        if path == workshop.absolute() / ops.LEDGER:
            raise OSError("Installed reader qualification fault")
        writer(path, data, **kwargs)

    operation_id = ""
    with patch.object(ops, "atomic_write", fail_ledger):
        try:
            ops.promote(workshop, "candidates/ExampleB.json", dry_run=False)
        except ops.PendingOperation as exc:
            operation_id = exc.operation_id
    if not operation_id:
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
            recovered = run_script(
                scrape, Path(sys.executable), "promote.py", "--recover", operation_id
            )
            if recovered["returncode"] != 0:
                raise RuntimeError(f"Installed reader recovery failed: {recovered}")
            query = await client.call_tool("lora_who", {"trigger": "red hair"})
            content = query.content[0]
            if content.type != "text" or json.loads(content.text)["loras"] != [
                "ExampleB"
            ]:
                raise RuntimeError("Installed workshop did not refresh after recovery")

    asyncio.run(asyncio.wait_for(smoke_workshop(), timeout=30))
    capture_results = smoke_capture()
    print(
        json.dumps(
            {
                "status": "passed",
                "version": bamboozle.__version__,
                "package_file": str(package_file),
                "capture_results": capture_results,
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
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
