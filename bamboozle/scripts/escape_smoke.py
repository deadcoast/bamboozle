"""Execute a marker-bearing streaming program through installed CLI and stdio MCP."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from bamboozle import markers
from bamboozle.agent_development import AGENT_TOOLS
from bamboozle.markers import quote_prefixes
from bamboozle.utils import json_bytes, sha256

SOURCE = dedent("""\
    PREFIXES = {"literal": "__SCM_LIT_", "case": "__SCM_CASE_"}
    EXAMPLES = ("__SCM_LIT_unknown__", "__SCM_CASE_bad", "__scm_lit_", "__SCM_CASE__SCM_LIT_")

    class FrameReader:
        def __init__(self, limit=8):
            if type(limit) is not int or limit < 1:
                raise ValueError("invalid limit")
            self.limit = limit
            self.reset()

        def reset(self):
            self.header = ""
            self.payload = ""
            self.kind = None
            self.state = "header"

        def feed(self, chunk):
            if self.state == "failed":
                raise ValueError("reset required")
            output = []
            try:
                for char in chunk:
                    if self.state == "header":
                        self.header += char
                        possible = {kind: prefix for kind, prefix in PREFIXES.items() if prefix.startswith(self.header)}
                        if not possible:
                            raise ValueError("invalid header")
                        for kind, prefix in possible.items():
                            if self.header == prefix:
                                self.kind, self.state = kind, "payload"
                                break
                    elif self.state == "payload":
                        if char == "_":
                            if not self.payload:
                                raise ValueError("empty payload")
                            self.state = "closing"
                        elif not char.isalnum() and char != "-":
                            raise ValueError("invalid payload")
                        else:
                            self.payload += char
                            if len(self.payload) > self.limit:
                                raise ValueError("payload limit")
                    elif char == "_":
                        output.append((self.kind, self.payload))
                        self.reset()
                    else:
                        raise ValueError("invalid closing")
            except ValueError:
                self.state = "failed"
                raise
            return output

        def finish(self):
            if self.state != "header" or self.header:
                self.state = "failed"
                raise ValueError("incomplete stream")

    def fuck(chunks, limit=8):
        reader = FrameReader(limit)
        output = []
        for chunk in chunks:
            output.extend(reader.feed(chunk))
        reader.finish()
        return output
""")

# These are newly authored neutral snippets. Quoting requires no inverse mapping.
BATCH = quote_prefixes(
    dedent("""\
    from collections import Counter
    from parser import assemble

    RAW_EXAMPLE = "__SCM_LIT_"

    def summarize(chunks, limit=64):
        rows = assemble(chunks, limit)
        return {kind: dict(sorted(Counter(value for tag, value in rows if tag == kind).items())) for kind in sorted({tag for tag, _ in rows})}
""")
)
RUNNER = quote_prefixes(
    dedent("""\
    import json
    from parser import PREFIXES, EXAMPLES, FrameReader, assemble
    from batch import RAW_EXAMPLE, summarize

    def frame(kind, value):
        return PREFIXES[kind] + value + "__"

    stream = frame("literal", "one") + frame("case", "two") + frame("literal", "one") + frame("case", "東京")
    expected = [("literal", "one"), ("case", "two"), ("literal", "one"), ("case", "東京")]
    assert RAW_EXAMPLE == PREFIXES["literal"]
    assert EXAMPLES == ("__SCM_LIT_unknown__", "__SCM_CASE_bad", "__scm_lit_", "__SCM_CASE__SCM_LIT_")
    split_cases = 0
    for left in range(len(stream) + 1):
        for right in range(left, len(stream) + 1):
            assert assemble([stream[:left], stream[left:right], stream[right:]]) == expected
            split_cases += 1
    assert assemble(iter(stream)) == expected
    assert summarize(iter(stream)) == {"case": {"two": 1, "東京": 1}, "literal": {"one": 2}}
    assert assemble([frame("literal", "x" * 64)]) == [("literal", "x" * 64)]
    assert assemble([]) == [] and summarize([]) == {}
    bad_inputs = ["bad", frame("literal", ""), frame("case", "x" * 65), frame("literal", "line\\nbreak"), PREFIXES["literal"], frame("case", "item")[:-1], frame("case", "item")[:-1] + "x"]
    for value in bad_inputs:
        reader = FrameReader(64)
        try:
            reader.feed(value)
            reader.finish()
        except ValueError:
            pass
        else:
            raise AssertionError(value)
        try:
            reader.feed("")
        except ValueError:
            pass
        else:
            raise AssertionError("failure was not retained")
        reader.reset()
        assert reader.feed(frame("literal", "ok")) == [("literal", "ok")]
        reader.finish()
    print(json.dumps({"split_cases": split_cases, "invalid_streams": len(bad_inputs), "frames": len(expected), "reset_after_failure": True}))
""")
)


def execute(root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(root / "runner.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    report = json.loads(result.stdout)
    if report["split_cases"] < 1000 or report["invalid_streams"] != 7:
        raise RuntimeError("Streaming scenario did not execute its boundary cases")
    return report


def run() -> dict:
    roots = Path("escaped-workflow").resolve()
    roots.mkdir()
    original = b"\xef\xbb\xbf" + SOURCE.replace("\n", "\r\n").encode()
    expected = original.replace(b"limit=8", b"limit=64")
    results = {}
    for mode in ("cli", "mcp"):
        root = roots / mode
        root.mkdir()
        (root / "parser.py").write_bytes(original)
        (root / "mapping.dictionary.json").write_bytes(json_bytes({"fuck": "assemble"}))

        def cli(*args: str, root: Path = root) -> dict:
            result = subprocess.run(
                [sys.executable, "-m", "bamboozle", "--root", str(root), *args],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            return json.loads(result.stdout)

        # All new encodings retain the marker protocol in their private context.
        cli("encode", "parser.py", "--dict", "mapping.dictionary.json")
        ledger = json.loads((root / ".parser_safe.py.scm_ledger.json").read_bytes())
        if ledger["schema_version"] != 3 or ledger["marker_version"] != 2:
            raise RuntimeError("New ledger omitted marker context")
        cli("decode", "parser_safe.py", "--strict", "-o", "exact.py")
        if (root / "exact.py").read_bytes() != original:
            raise RuntimeError("Strict decoding lost original marker/BOM/CRLF bytes")
        policy = {
            "schema_version": 1,
            "files": {"parser.py": {"policy": "translate"}},
            "new_files": {".": "translate"},
        }
        (root / "policy.json").write_bytes(json_bytes(policy))
        exported = cli(
            "view-export",
            "policy.json",
            "--output",
            "view",
            "--dict",
            "mapping.dictionary.json",
        )
        view = root / "view"
        (view / "parser.py").write_bytes(
            (view / "parser.py").read_bytes().replace(b"limit=8", b"limit=64")
        )
        (view / "batch.py").write_text(BATCH, encoding="utf-8", newline="")
        (view / "runner.py").write_text(RUNNER, encoding="utf-8", newline="")
        neutral = execute(view)
        cli("keygen", "--key", "transport.key")
        cli(
            "seal",
            "view/parser.py",
            "--key",
            "transport.key",
            "--recipient",
            "operator",
            "-o",
            "transport.bam",
        )
        cli(
            "open",
            "transport.bam",
            "--key",
            "transport.key",
            "--recipient",
            "operator",
            "-o",
            "opened.py",
        )
        if (root / "opened.py").read_bytes() != (view / "parser.py").read_bytes():
            raise RuntimeError("Encryption changed escaped neutral source")
        cli(
            "decode",
            "opened.py",
            "--ledger",
            ".parser_safe.py.scm_ledger.json",
            "-o",
            "decrypted_ready.py",
        )
        if (root / "decrypted_ready.py").read_bytes() != expected:
            raise RuntimeError(
                "Composed decryption/translation lost edited original bytes"
            )

        if mode == "cli":
            review = cli("view-review", exported["view_id"])
            applied = cli(
                "view-apply",
                exported["view_id"],
                "--review-sha256",
                review["review_sha256"],
            )
            agent = None
        else:

            async def stdio(
                root: Path = root, exported: dict = exported
            ) -> tuple[dict, dict, dict]:
                async def call(client: Client, name: str, arguments: dict) -> dict:
                    response = await client.call_tool(name, arguments)
                    if response.is_error or not isinstance(response.data, dict):
                        raise RuntimeError(f"Escape MCP failed: {name}")
                    return response.data

                transport = StdioTransport(
                    command=sys.executable,
                    args=["-m", "bamboozle", "--root", str(root), "serve"],
                    cwd=str(root),
                )
                async with Client(transport) as client:
                    review = await call(
                        client, "review_project_view", {"view_id": exported["view_id"]}
                    )
                    applied = await call(
                        client,
                        "apply_project_view",
                        {
                            "view_id": exported["view_id"],
                            "review_sha256": review["review_sha256"],
                        },
                    )
                # A distinct source-agent connection exposes only source-development tools.
                agent_root = root / "source-agent"
                agent_root.mkdir()
                (agent_root / "parser.py").write_bytes(original)
                (agent_root / "mapping.dictionary.json").write_bytes(
                    json_bytes({"fuck": "assemble"})
                )
                transport = StdioTransport(
                    command=sys.executable,
                    args=[
                        "-m",
                        "bamboozle",
                        "--root",
                        str(agent_root),
                        "serve-agent",
                        "--dict",
                        "mapping.dictionary.json",
                    ],
                    cwd=str(agent_root),
                )
                async with Client(transport) as client:
                    names = sorted(tool.name for tool in await client.list_tools())
                    if set(names) != AGENT_TOOLS:
                        raise RuntimeError("Source-agent capabilities changed")
                    read = await call(
                        client, "read_safe_file", {"file_path": "parser.py"}
                    )
                    written = await call(
                        client,
                        "write_safe_file",
                        {
                            "file_path": "parser.py",
                            "content": read["content"].replace("limit=8", "limit=64"),
                        },
                    )
                    for name, content in (("batch.py", BATCH), ("runner.py", RUNNER)):
                        await call(
                            client,
                            "write_safe_file",
                            {"file_path": name, "content": content},
                        )
                if (agent_root / "parser.py").read_bytes() != expected:
                    raise RuntimeError("Source-agent return lost original syntax")
                return (
                    review,
                    applied,
                    {
                        "root": str(agent_root),
                        "tools": names,
                        "read_marker_version": read["marker_version"],
                        "write": written,
                        "execution": execute(agent_root),
                    },
                )

            review, applied, agent = asyncio.run(asyncio.wait_for(stdio(), timeout=90))
        restored = execute(root)
        if (root / "parser.py").read_bytes() != expected or not applied[
            "receipt_saved"
        ]:
            raise RuntimeError("View import did not retain the complete source result")
        results[mode] = {
            "root": str(root),
            "export": exported,
            "review": review,
            "apply": applied,
            "neutral_execution": neutral,
            "restored_execution": restored,
            "source_agent": agent,
            "strict_bytes_preserved": True,
            "encrypted_edited_bytes_preserved": True,
            "original_sha256": sha256(original),
            "restored_sha256": sha256(expected),
        }
    return {
        "marker_module": str(Path(markers.__file__).resolve()),
        "workflows": results,
    }
