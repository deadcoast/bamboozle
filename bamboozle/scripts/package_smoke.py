"""Exercise an installed wheel from a directory outside the source package."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

import bamboozle
from bamboozle.dictionary import Dictionary
from workshop.scrape.alias_pass import Taxonomy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args()
    package_file = Path(bamboozle.__file__).resolve()
    if not package_file.is_relative_to(args.prefix.resolve()):
        raise RuntimeError(
            f"Smoke test loaded a source checkout instead of the wheel: {package_file}"
        )
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
    print(
        json.dumps(
            {
                "status": "passed",
                "version": bamboozle.__version__,
                "package_file": str(package_file),
                "checks": [
                    "bundled_resources",
                    "cli_roundtrip",
                    "ledger_verify",
                    "tessera_roundtrip",
                    "baseline_audit",
                    "mcp_stdio_tool_call",
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
