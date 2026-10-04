"""Execute source/installed CLI and stdio return validation with real returned code."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from bamboozle.utils import json_bytes, sha256


def run() -> dict:
    roots = Path("returned-workflow").resolve()
    roots.mkdir()
    results = {}
    original = b"\xef\xbb\xbfdef fuck(values):\r\n    return sum(values)\r\nassert fuck([3, 4]) == 7\r\n"

    def execute(path: Path) -> None:
        subprocess.run(
            [sys.executable, str(path)], capture_output=True, check=True, timeout=10
        )

    for mode in ("cli", "mcp"):
        root = roots / mode
        root.mkdir()
        (root / "source.py").write_bytes(original)
        (root / "mapping.dictionary.json").write_bytes(json_bytes({"fuck": "assemble"}))

        def cli(*args: str, code: int = 0, root: Path = root) -> dict:
            result = subprocess.run(
                [sys.executable, "-m", "bamboozle", "--root", str(root), *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if result.returncode != code:
                raise RuntimeError(
                    f"Return CLI {args[0]} exited {result.returncode}: {result.stdout} {result.stderr}"
                )
            return json.loads(result.stdout)

        cli("encode", "source.py", "--dict", "mapping.dictionary.json")
        accepted = cli("validate-return", "source_safe.py", "--original", "source.py")
        returned = root / "source_safe.py"
        (root / "baseline.py").write_bytes(returned.read_bytes())
        edited = (
            returned.read_bytes()
            .replace(b"sum(values)", b"sum(v * v for v in values)")
            .replace(b"== 7", b"== 25")
            + b"# assembling needs explicit vocabulary review\r\n"
        )
        returned.write_bytes(edited)
        execute(returned)
        review = cli(
            "validate-return", "source_safe.py", "--original", "source.py", code=3
        )
        if review["status"] != "needs_review" or set(review["counts"]) != {
            "ambiguous_target",
            "unlisted_variant",
        }:
            raise RuntimeError("Installed validator omitted target/variant evidence")
        (root / "malformed.py").write_bytes(edited + b"__SCM_LIT_unknown__\n")
        refused = cli(
            "validate-return",
            "malformed.py",
            "--original",
            "source.py",
            "--ledger",
            ".source_safe.py.scm_ledger.json",
            code=3,
        )
        if refused["status"] != "refused" or refused["decodable"]:
            raise RuntimeError("Malformed returned marker was not refused")
        # Operator encryption encloses edited neutral bytes. The saved semantic
        # dictionary/context is still used after decryption, never the crypto key.
        cli("keygen", "--key", "return.key")
        sealed = cli(
            "seal",
            "source_safe.py",
            "--key",
            "return.key",
            "--recipient",
            "return-operator",
            "-o",
            "return.bam",
        )
        opened = cli(
            "open",
            "return.bam",
            "--key",
            "return.key",
            "--recipient",
            "return-operator",
            "-o",
            "decrypted.py",
        )
        if (root / "decrypted.py").read_bytes() != edited:
            raise RuntimeError(
                "Encrypted edited payload did not restore its exact bytes"
            )
        decrypted = cli(
            "validate-return",
            "decrypted.py",
            "--original",
            "source.py",
            "--ledger",
            ".source_safe.py.scm_ledger.json",
            code=3,
        )
        if decrypted["validation_sha256"] != review["validation_sha256"]:
            raise RuntimeError(
                "Decrypted bytes were checked under different semantic inputs"
            )

        (root / "src").mkdir()
        (root / "src/core.py").write_bytes(original)
        (root / "data.json").write_bytes(b'{"prompt":"fuck", "limit":2}')
        (root / "guide.md").write_bytes(b"fuck\n\n`python src/core.py`\n")
        policy = {
            "schema_version": 2,
            "files": {
                "src/core.py": {"policy": "translate"},
                "data.json": {
                    "policy": "translate",
                    "document": {"format": "json", "pointers": ["/prompt"]},
                },
                "guide.md": {"policy": "translate", "document": {"format": "markdown"}},
            },
            "new_files": {"src": "translate"},
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
        (view / "src/core.py").write_bytes(edited)
        (view / "src/batch.py").write_bytes(
            b"from core import assemble\nassert assemble([3,4]) == 25\nassert assemble([]) == 0\nassert assemble([-2,5]) == 29\nprint('returned-pipeline-ok')\n"
        )
        (view / "data.json").write_bytes(b'{"prompt":"assemble generated", "limit":5}')
        (view / "guide.md").write_bytes(b"assemble\n\n`python src/batch.py`\n")
        execute(view / "src/batch.py")

        def verify_view(report: dict) -> None:
            files = {
                item["path"]: item for item in report["return_validation"]["files"]
            }
            if (
                not report["ok"]
                or report["return_validation"]["status"] != "needs_review"
                or "protected_json_changed" not in files["data.json"]["counts"]
                or "protected_markdown_changed" not in files["guide.md"]["counts"]
                or files["src/batch.py"]["baseline_available"]
            ):
                raise RuntimeError(
                    "Installed view review lost structured or new-file diagnostics"
                )

        if mode == "cli":
            arguments = (
                "restore-edits",
                "decrypted.py",
                "--original",
                "source.py",
                "--ledger",
                ".source_safe.py.scm_ledger.json",
                "--terms",
                "translate",
                "-o",
                "restored.py",
            )
            restored_review = cli(*arguments)
            restored = cli(
                *arguments,
                "--apply",
                "--review-sha256",
                restored_review["review_sha256"],
            )
            reviewed = cli("view-review", exported["view_id"])
            verify_view(reviewed)
            page = cli(
                "view-review",
                exported["view_id"],
                "--validation-path",
                "src/batch.py",
                "--max-findings",
                "1",
                "--offset",
                "1",
            )
            if (
                page["review_sha256"] != reviewed["review_sha256"]
                or page["return_validation"]["files"][0]["findings"][0]["index"] != 1
            ):
                raise RuntimeError(
                    "Installed CLI view pagination changed the review or returned the wrong page"
                )
            applied = cli(
                "view-apply",
                exported["view_id"],
                "--review-sha256",
                reviewed["review_sha256"],
            )
            operator = {"review": reviewed, "apply": applied, "restoration": restored}
        else:

            async def stdio(
                root: Path = root,
                decrypted: dict = decrypted,
                exported: dict = exported,
            ) -> dict:
                transport = StdioTransport(
                    command=sys.executable,
                    args=["-m", "bamboozle", "--root", str(root), "serve"],
                    cwd=str(root),
                )
                async with Client(transport) as client:
                    tools = {tool.name: tool for tool in await client.list_tools()}
                    annotations = tools["validate_returned_file"].annotations
                    if annotations is None or annotations.read_only_hint is not True:
                        raise RuntimeError(
                            "Return validation is not advertised read-only"
                        )

                    async def call(name: str, arguments: dict) -> dict:
                        result = await client.call_tool(name, arguments)
                        if result.is_error or not isinstance(result.data, dict):
                            raise RuntimeError(f"Installed return MCP failed: {name}")
                        return result.data

                    accepted_mcp = await call(
                        "validate_returned_file",
                        {
                            "source": "baseline.py",
                            "original": "source.py",
                            "ledger": ".source_safe.py.scm_ledger.json",
                        },
                    )
                    if accepted_mcp["status"] != "accepted":
                        raise RuntimeError("Operator MCP did not accept exact baseline")
                    checked = await call(
                        "validate_returned_file",
                        {
                            "source": "decrypted.py",
                            "original": "source.py",
                            "ledger": ".source_safe.py.scm_ledger.json",
                        },
                    )
                    if checked["validation_sha256"] != decrypted["validation_sha256"]:
                        raise RuntimeError(
                            "Operator MCP did not use the saved dictionary"
                        )
                    bad = await call(
                        "validate_returned_file",
                        {
                            "source": "malformed.py",
                            "original": "source.py",
                            "ledger": ".source_safe.py.scm_ledger.json",
                        },
                    )
                    if bad["status"] != "refused":
                        raise RuntimeError(
                            "Operator MCP did not refuse malformed return"
                        )
                    arguments = {
                        "source": "decrypted.py",
                        "original": "source.py",
                        "ledger": ".source_safe.py.scm_ledger.json",
                        "terms": "translate",
                        "output": "restored.py",
                    }
                    preview = await call("preview_restoration", arguments)
                    restored_mcp = await call(
                        "apply_preview", {"plan_id": preview["plan_id"]}
                    )
                    reviewed_mcp = await call(
                        "review_project_view", {"view_id": exported["view_id"]}
                    )
                    verify_view(reviewed_mcp)
                    page = await call(
                        "review_project_view",
                        {
                            "view_id": exported["view_id"],
                            "validation_path": "src/batch.py",
                            "validation_offset": 1,
                            "max_findings": 1,
                        },
                    )
                    if (
                        page["review_sha256"] != reviewed_mcp["review_sha256"]
                        or page["return_validation"]["files"][0]["findings"][0]["index"]
                        != 1
                    ):
                        raise RuntimeError("Operator MCP lost paged view diagnostics")
                    applied_mcp = await call(
                        "apply_project_view",
                        {
                            "view_id": exported["view_id"],
                            "review_sha256": reviewed_mcp["review_sha256"],
                        },
                    )
                    return {
                        "accepted": accepted_mcp,
                        "validation": checked,
                        "refused": bad,
                        "review": reviewed_mcp,
                        "apply": applied_mcp,
                        "restoration": restored_mcp,
                    }

            operator = asyncio.run(asyncio.wait_for(stdio(), timeout=90))
        execute(root / "restored.py")
        execute(root / "src/batch.py")
        cli("verify-restoration", "restored.py")
        if (
            (root / "source.py").read_bytes() != original
            or b"from core import fuck" not in (root / "src/batch.py").read_bytes()
            or not operator["apply"]["receipt_saved"]
        ):
            raise RuntimeError("Returned source was not restored completely")
        results[mode] = {
            "root": str(root),
            "accepted": accepted,
            "diagnostics": review,
            "refused": refused,
            "operator": operator,
            "sealed": sealed,
            "opened": opened,
            "encrypted_edited_bytes_preserved": True,
            "neutral_and_restored_programs_executed": True,
            "retained_original_sha256": sha256(original),
        }
    return results
