"""Run complete CLI/MCP Python application development and restoration workflows."""

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

from bamboozle import cipher_operations, classical, python_project, python_source
from bamboozle.utils import json_bytes, sha256


def run(workspace: Path | None = None) -> dict:
    scenario = runpy.run_path(str(Path(__file__).with_name("python_scenario.py")))
    roots = (workspace or Path("python-workflow")).resolve()
    roots.mkdir(parents=True)
    results = {}
    rules = scenario["rules"]()
    dictionary = {
        "task": "stage",
        "build": "assemble",
        "run": "execute",
        "weight": "effort",
        "total": "sum_cost",
    }

    def application(root: Path, *, enhanced: bool) -> dict:
        arguments = [sys.executable, "-m", "pipeline.main", "workload.json"]
        if enhanced:
            arguments += ["--capacity", "2"]
        completed = subprocess.run(
            arguments, cwd=root, capture_output=True, text=True, check=True, timeout=20
        )
        result = json.loads(completed.stdout)
        if result["total"] != 10 or result["critical"] != 8:
            raise RuntimeError("Application CLI returned an incorrect dependency plan")
        if enhanced and result["makespan"] != 8:
            raise RuntimeError(
                "Application CLI did not apply the agent's scheduling enhancement"
            )
        return result

    def benchmark(root: Path) -> dict:
        completed = subprocess.run(
            [sys.executable, "bench.py"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        report = json.loads(completed.stdout)
        if (
            report["scheduler_executions"] != 300
            or report["invalid_workloads"] != 12
            or not report["protected_literals_preserved"]
        ):
            raise RuntimeError(
                "Returned application did not complete its workload/error coverage"
            )
        return report

    for mode in ("cli", "mcp"):
        root = roots / mode
        root.mkdir()
        originals = scenario["originals"](root)
        (root / "mapping.dictionary.json").write_bytes(json_bytes(dictionary))
        (root / "transfer.cipher-recipe.json").write_bytes(
            json_bytes(
                {
                    "schema_version": 1,
                    "steps": [
                        {
                            "cipher": "vigenere",
                            "variant": "ascii-v1",
                            "params": {"key": "LEMON"},
                        },
                        {
                            "cipher": "columnar-transposition",
                            "variant": "ragged-v1",
                            "params": {"key": "BALLOON"},
                        },
                        {
                            "cipher": "polybius-square",
                            "variant": "framed5-v1",
                            "params": {},
                        },
                        {
                            "cipher": "rail-fence",
                            "variant": "zigzag-v1",
                            "params": {"rails": 5},
                        },
                    ],
                }
            )
        )
        policy = {
            "schema_version": 2,
            "files": {
                name: {"policy": "translate", "document": rules}
                if name.endswith(".py")
                else {"policy": "copy"}
                for name in originals
            },
            "new_files": {".": {"policy": "translate", "document": rules}},
        }
        (root / "policy.json").write_bytes(json_bytes(policy))
        original_run = application(root, enhanced=False)

        def cli(*arguments: str, root: Path = root) -> dict:
            completed = subprocess.run(
                [sys.executable, "-m", "bamboozle", "--root", str(root), *arguments],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            return json.loads(completed.stdout)

        selection = ["--format", "python"]
        for option, values in (
            ("--python-identifier", rules["identifiers"]),
            ("--python-module", rules["modules"]),
            ("--python-string", rules["strings"]),
        ):
            for value in values:
                selection.extend((option, value))
        cli(
            "encode",
            "pipeline/graph.py",
            "--dict",
            "mapping.dictionary.json",
            *selection,
        )
        cli("decode", "pipeline/graph_safe.py", "--strict", "-o", "strict.py")
        if (root / "strict.py").read_bytes() != originals["pipeline/graph.py"]:
            raise RuntimeError("Saved Python context lost exact original bytes")

        def edit_and_check(
            exported: dict,
            root: Path = root,
            cli=cli,
            original_run: dict = original_run,
        ) -> dict:
            view = root / "view"
            baseline = application(view, enhanced=False)
            if baseline != original_run:
                raise RuntimeError("Neutral baseline changed application behavior")
            scenario["agent_edit"](view)
            neutral_run = application(view, enhanced=True)
            neutral_bench = benchmark(view)
            # Transport confidentiality composes with the selected Python decoder.
            cli("keygen", "--key", "transport.key")
            cli(
                "seal",
                "view/pipeline/main.py",
                "--key",
                "transport.key",
                "--recipient",
                "operator",
                "-o",
                "return.bam",
            )
            cli(
                "open",
                "return.bam",
                "--key",
                "transport.key",
                "--recipient",
                "operator",
                "-o",
                "decrypted.py",
            )
            if (root / "decrypted.py").read_bytes() != (
                view / "pipeline/main.py"
            ).read_bytes():
                raise RuntimeError("Encrypted Python return lost its exact bytes")
            return {
                "export": exported,
                "neutral_baseline": baseline,
                "neutral_enhanced": neutral_run,
                "neutral_benchmark": neutral_bench,
            }

        def encrypt_classical(cli=cli) -> None:
            cli(
                "seal",
                "classical.bin",
                "--key",
                "transport.key",
                "--recipient",
                "operator",
                "-o",
                "cipher-return.bam",
            )
            cli(
                "open",
                "cipher-return.bam",
                "--key",
                "transport.key",
                "--recipient",
                "operator",
                "-o",
                "classical-opened.bin",
            )

        def accept_classical(
            flow: dict, forward: dict, reverse: dict, verified: dict, root: Path = root
        ) -> None:
            returned = (root / "classical-return.py").read_bytes()
            view_source = root / "view/pipeline/main.py"
            if returned != view_source.read_bytes() or verified["status"] != "verified":
                raise RuntimeError(
                    "Classical/encrypted transport changed the complete returned program"
                )
            # The actual decrypted/reversed bytes enter the reviewed view import.
            view_source.write_bytes(returned)
            if application(root / "view", enhanced=True) != flow["neutral_enhanced"]:
                raise RuntimeError(
                    "Transported neutral application failed its executable contract"
                )
            flow["classical_transport"] = {
                "forward": forward,
                "reverse": reverse,
                "verified": verified,
                "exact_return_bytes": True,
                "application_executed_after_transport": True,
            }

        if mode == "cli":
            exported = cli(
                "view-export",
                "policy.json",
                "--output",
                "view",
                "--dict",
                "mapping.dictionary.json",
            )
            flow = edit_and_check(exported)
            command: tuple[str, ...] = (
                "cipher-transform",
                "view/pipeline/main.py",
                "--recipe",
                "transfer.cipher-recipe.json",
                "-o",
                "classical.bin",
            )
            preview = cli(*command)
            forward = cli(
                *command, "--apply", "--review-sha256", preview["review_sha256"]
            )
            encrypt_classical()
            command = (
                "cipher-transform",
                "classical-opened.bin",
                "--recipe",
                "transfer.cipher-recipe.json",
                "--reverse",
                "-o",
                "classical-return.py",
            )
            preview = cli(*command)
            reverse = cli(
                *command, "--apply", "--review-sha256", preview["review_sha256"]
            )
            verified = cli("cipher-verify", "classical-return.py")
            accept_classical(flow, forward, reverse, verified)
            review = cli("view-review", exported["view_id"])
            applied = cli(
                "view-apply",
                exported["view_id"],
                "--review-sha256",
                review["review_sha256"],
            )
        else:

            async def operate(
                root: Path = root,
                edit_and_check=edit_and_check,
                encrypt_classical=encrypt_classical,
                accept_classical=accept_classical,
            ) -> tuple[dict, dict, dict]:
                transport = StdioTransport(
                    command=sys.executable,
                    args=[
                        "-m",
                        "bamboozle",
                        "--root",
                        str(root),
                        "serve",
                        "--dict",
                        "mapping.dictionary.json",
                    ],
                    cwd=str(root),
                )
                async with Client(transport) as client:

                    async def call(tool_name: str, arguments: dict) -> dict:
                        response = await client.call_tool(tool_name, arguments)
                        if response.is_error or not isinstance(response.data, dict):
                            raise RuntimeError(
                                f"Python workflow MCP failed: {tool_name}"
                            )
                        return response.data

                    preview = await call(
                        "preview_file",
                        {
                            "source": "pipeline/graph.py",
                            "output": "mcp_graph.py",
                            "document_format": "python",
                            "python_identifiers": rules["identifiers"],
                            "python_modules": rules["modules"],
                            "python_strings": rules["strings"],
                        },
                    )
                    await call("apply_preview", {"plan_id": preview["plan_id"]})
                    validation = await call(
                        "validate_returned_file",
                        {"source": "mcp_graph.py", "original": "pipeline/graph.py"},
                    )
                    if validation["status"] != "accepted":
                        raise RuntimeError(
                            "Operator MCP rejected a valid complete Python baseline"
                        )
                    exported = await call(
                        "export_project_view",
                        {"config": "policy.json", "output": "view"},
                    )
                    flow = edit_and_check(exported)
                    catalog = await call("cipher_catalog", {})
                    if len(catalog["classical"]["ciphers"]) != 11:
                        raise RuntimeError(
                            "Operator did not expose all supplied cipher families"
                        )
                    preview = await call(
                        "preview_cipher_transform",
                        {
                            "source": "view/pipeline/main.py",
                            "recipe": "transfer.cipher-recipe.json",
                            "output": "classical.bin",
                        },
                    )
                    forward = await call(
                        "apply_preview", {"plan_id": preview["plan_id"]}
                    )
                    encrypt_classical()
                    preview = await call(
                        "preview_cipher_transform",
                        {
                            "source": "classical-opened.bin",
                            "recipe": "transfer.cipher-recipe.json",
                            "output": "classical-return.py",
                            "reverse": True,
                        },
                    )
                    reverse = await call(
                        "apply_preview", {"plan_id": preview["plan_id"]}
                    )
                    verified = await call(
                        "verify_cipher_transform", {"source": "classical-return.py"}
                    )
                    accept_classical(flow, forward, reverse, verified)
                    review = await call(
                        "review_project_view", {"view_id": exported["view_id"]}
                    )
                    applied = await call(
                        "apply_project_view",
                        {
                            "view_id": exported["view_id"],
                            "review_sha256": review["review_sha256"],
                        },
                    )
                    flow["single_file_validation"] = validation
                    return flow, review, applied

            flow, review, applied = asyncio.run(asyncio.wait_for(operate(), timeout=90))
        restored_run = application(root, enhanced=True)
        restored_bench = benchmark(root)
        if (
            restored_run != flow["neutral_enhanced"]
            or restored_bench != flow["neutral_benchmark"]
            or not applied["receipt_saved"]
        ):
            raise RuntimeError("Neutral/restored application results differ")
        if (root / "pipeline/graph.py").read_bytes() != originals[
            "pipeline/graph.py"
        ] or (root / "pipeline/model.py").read_bytes() != originals[
            "pipeline/model.py"
        ]:
            raise RuntimeError(
                "Unedited original source bytes changed during project return"
            )
        results[mode] = {
            "root": str(root),
            **flow,
            "review": review,
            "apply": applied,
            "original_execution": original_run,
            "restored_execution": restored_run,
            "restored_benchmark": restored_bench,
            "original_sha256": {name: sha256(raw) for name, raw in originals.items()},
            "strict_bytes_preserved": True,
            "encrypted_return_bytes_preserved": True,
        }
    return {
        "workflows": results,
        "source_module": str(Path(python_source.__file__).resolve()),
        "project_module": str(Path(python_project.__file__).resolve()),
        "classical_module": str(Path(classical.__file__).resolve()),
        "cipher_operations_module": str(Path(cipher_operations.__file__).resolve()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="New directory for complete retained source/neutral/restored application artifacts",
    )
    print(json.dumps(run(parser.parse_args().workspace), indent=2))
