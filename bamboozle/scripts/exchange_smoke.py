"""Execute a dependent application across separate source and neutral receiver roots."""

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

from bamboozle import identity, project_exchange
from bamboozle.tessera import create_key
from bamboozle.utils import json_bytes, sha256
from bamboozle.workspace import Workspace


def run(workspace: Path | None = None, *, managed: bool = False) -> dict:
    scenario = runpy.run_path(str(Path(__file__).with_name("python_scenario.py")))
    roots = (workspace or Path("exchange-workflow")).resolve()
    roots.mkdir(parents=True)
    results = {}
    recipe = {
        "schema_version": 1,
        "steps": [
            {"cipher": "vigenere", "variant": "ascii-v1", "params": {"key": "LEMON"}},
            {
                "cipher": "columnar-transposition",
                "variant": "ragged-v1",
                "params": {"key": "BALLOON"},
            },
            {"cipher": "polybius-square", "variant": "framed5-v1", "params": {}},
            {"cipher": "rail-fence", "variant": "zigzag-v1", "params": {"rails": 5}},
        ],
    }
    credentials = {
        "key": "transport.key",
        "recipient": "development",
        "recipe": "transfer.cipher-recipe.json",
    }

    if managed:
        credentials = {"peer": "receiver", "recipe": "transfer.cipher-recipe.json"}
    receiver_credentials = {**credentials, **({"peer": "owner"} if managed else {})}

    def execute(root: Path, enhanced: bool) -> dict:
        arguments = [sys.executable, "-m", "pipeline.main", "workload.json"]
        if enhanced:
            arguments += ["--capacity", "2"]
        completed = subprocess.run(
            arguments, cwd=root, capture_output=True, text=True, check=True, timeout=20
        )
        result = json.loads(completed.stdout)
        if (
            result["total"] != 10
            or result["critical"] != 8
            or (enhanced and result["makespan"] != 8)
        ):
            raise RuntimeError(
                "Exchanged application failed its dependency/scheduling contract"
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
        result = json.loads(completed.stdout)
        if (
            result["scheduler_executions"] != 300
            or result["invalid_workloads"] != 12
            or not result["protected_literals_preserved"]
        ):
            raise RuntimeError(
                "Exchanged application failed complete workload/error coverage"
            )
        return result

    for mode in ("cli", "mcp"):
        owner, receiver = roots / mode / "source", roots / mode / "receiver"
        owner.mkdir(parents=True)
        receiver.mkdir()
        originals = scenario["originals"](owner)
        originals["obsolete.py"] = b"OLD = True\n"
        (owner / "obsolete.py").write_bytes(originals["obsolete.py"])
        rules = scenario["rules"]()
        (owner / "mapping.dictionary.json").write_bytes(
            json_bytes(
                {
                    "task": "stage",
                    "build": "assemble",
                    "run": "execute",
                    "weight": "effort",
                    "total": "sum_cost",
                }
            )
        )
        (owner / "policy.json").write_bytes(
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
        create_key(Workspace(owner), "transport.key")
        # Explicit synthetic key/recipe provisioning, separate from project payloads.
        (receiver / "transport.key").write_bytes((owner / "transport.key").read_bytes())
        for root in (owner, receiver):
            (root / "transfer.cipher-recipe.json").write_bytes(json_bytes(recipe))
        baseline = execute(owner, False)

        def cli(root: Path, *arguments: str) -> dict:
            completed = subprocess.run(
                [sys.executable, "-m", "bamboozle", "--root", str(root), *arguments],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            result = json.loads(completed.stdout)
            if result.get("ok") is False or result.get("status") == "error":
                raise RuntimeError(f"Exchange CLI failed: {result}")
            return result

        def agent_work(
            opened: dict,
            owner: Path = owner,
            receiver: Path = receiver,
            baseline: dict = baseline,
        ) -> dict:
            view = Path(opened["agent_context"]["workspace"])
            if view != receiver / "work" or execute(view, False) != baseline:
                raise RuntimeError(
                    "Receiver did not get an executable neutral baseline"
                )
            snapshot = project_exchange.snapshot_view(Workspace(view))
            if any(
                name.endswith((".key", ".dictionary.json", ".cipher-recipe.json"))
                for name in snapshot
            ):
                raise RuntimeError(
                    "Private operator inputs entered the neutral workspace"
                )
            if b"def task" in (view / "pipeline/model.py").read_bytes():
                raise RuntimeError(
                    "Original selected identifiers entered the neutral workspace"
                )
            scenario["agent_edit"](view)
            (view / "obsolete.py").unlink()
            return {
                "neutral_execution": execute(view, True),
                "neutral_benchmark": benchmark(view),
            }

        if managed and mode == "cli":
            for root, name, peer in (
                (owner, "owner", "receiver"),
                (receiver, "receiver", "owner"),
            ):
                cli(root, "identity-init", name)
                cli(root, "recipient-register", peer, "--key", "transport.key")
        receiver_flags = [
            value
            for name, value in receiver_credentials.items()
            for value in ("--" + name, value)
        ]
        flags = [
            value
            for name, value in credentials.items()
            for value in ("--" + name, value)
        ]
        if mode == "cli":
            exported = cli(
                owner,
                "view-export",
                "policy.json",
                "--output",
                "view",
                "--dict",
                "mapping.dictionary.json",
            )
            sent = cli(owner, "view-pack", exported["view_id"], "-o", "out.bam", *flags)
            (receiver / "out.bam").write_bytes((owner / "out.bam").read_bytes())
            opened = cli(
                receiver, "exchange-open", "out.bam", "-o", "work", *receiver_flags
            )
            context = cli(receiver, "exchange-context", opened["exchange_id"])
            flow = agent_work(opened)
            returned = cli(
                receiver,
                "exchange-return",
                opened["exchange_id"],
                "-o",
                "return.bam",
                *receiver_flags,
            )
            (owner / "return.bam").write_bytes((receiver / "return.bam").read_bytes())
            review = cli(
                owner,
                "view-review",
                exported["view_id"],
                "--return-packet",
                "return.bam",
                "--delete",
                "obsolete.py",
                *flags,
            )
            applied = cli(
                owner,
                "view-apply",
                exported["view_id"],
                "--return-packet",
                "return.bam",
                "--delete",
                "obsolete.py",
                "--review-sha256",
                review["review_sha256"],
                *flags,
            )
        else:

            async def operate(
                owner: Path = owner, receiver: Path = receiver, agent_work=agent_work
            ) -> tuple:
                def transport(
                    transport_root: Path, dictionary: bool = False
                ) -> StdioTransport:
                    return StdioTransport(
                        command=sys.executable,
                        args=[
                            "-m",
                            "bamboozle",
                            "--root",
                            str(transport_root),
                            "serve",
                            *(
                                ["--dict", "mapping.dictionary.json"]
                                if dictionary
                                else []
                            ),
                        ],
                        cwd=str(transport_root),
                    )

                async with (
                    Client(transport(owner, True)) as source_client,
                    Client(transport(receiver)) as receiver_client,
                ):

                    async def call(client: Client, tool: str, arguments: dict) -> dict:
                        result = await client.call_tool(tool, arguments)
                        if (
                            result.is_error
                            or not isinstance(result.data, dict)
                            or result.data.get("ok") is False
                            or result.data.get("status") == "error"
                        ):
                            raise RuntimeError(
                                f"Exchange stdio MCP failed: {tool}: {result}"
                            )
                        return result.data

                    if managed:
                        for client, name, peer in (
                            (source_client, "owner", "receiver"),
                            (receiver_client, "receiver", "owner"),
                        ):
                            await call(client, "initialize_identity", {"name": name})
                            await call(
                                client,
                                "change_recipient",
                                {
                                    "peer": peer,
                                    "action": "register",
                                    "key": "transport.key",
                                },
                            )
                    exported = await call(
                        source_client,
                        "export_project_view",
                        {"config": "policy.json", "output": "view"},
                    )
                    sent = await call(
                        source_client,
                        "pack_project_view",
                        {
                            "view_id": exported["view_id"],
                            "output": "out.bam",
                            **credentials,
                        },
                    )
                    (receiver / "out.bam").write_bytes((owner / "out.bam").read_bytes())
                    opened = await call(
                        receiver_client,
                        "open_project_exchange",
                        {"packet": "out.bam", "output": "work", **receiver_credentials},
                    )
                    context = await call(
                        receiver_client,
                        "project_exchange_context",
                        {"exchange_id": opened["exchange_id"]},
                    )
                    flow = agent_work(opened)
                    returned = await call(
                        receiver_client,
                        "pack_project_return",
                        {
                            "exchange_id": opened["exchange_id"],
                            "output": "return.bam",
                            **receiver_credentials,
                        },
                    )
                    (owner / "return.bam").write_bytes(
                        (receiver / "return.bam").read_bytes()
                    )
                    arguments = {
                        "view_id": exported["view_id"],
                        "return_packet": "return.bam",
                        "delete_files": ["obsolete.py"],
                        **credentials,
                    }
                    review = await call(source_client, "review_project_view", arguments)
                    applied = await call(
                        source_client,
                        "apply_project_view",
                        {**arguments, "review_sha256": review["review_sha256"]},
                    )
                    return (
                        exported,
                        sent,
                        opened,
                        context,
                        flow,
                        returned,
                        review,
                        applied,
                    )

            exported, sent, opened, context, flow, returned, review, applied = (
                asyncio.run(asyncio.wait_for(operate(), timeout=90))
            )
        restored_run, restored_bench = execute(owner, True), benchmark(owner)
        if (
            restored_run != flow["neutral_execution"]
            or restored_bench != flow["neutral_benchmark"]
            or not applied["receipt_saved"]
        ):
            raise RuntimeError(
                "Restored application differs from the neutral receiver's implementation"
            )
        if (owner / "obsolete.py").exists():
            raise RuntimeError("Explicit returned deletion was not applied")
        directory, manifest, _, _, view = project_exchange.load_view(
            Workspace(owner), exported["view_id"]
        )
        expected = {
            name: entry["view_sha256"] for name, entry in manifest["files"].items()
        } | project_exchange.generated_contracts(manifest)
        if project_exchange._hashes(project_exchange.snapshot_view(view)) != expected:
            raise RuntimeError("Remote return modified the owner's local neutral view")
        for name in ("pipeline/model.py", "pipeline/graph.py"):
            if (owner / name).read_bytes() != originals[name]:
                raise RuntimeError("Unselected original source bytes changed on return")
        receipt = json.loads((directory / "result.json").read_bytes())
        if receipt["exchange_sha256"] != returned["exchange_sha256"]:
            raise RuntimeError(
                "Source completion lost its authenticated return binding"
            )
        if managed:
            for root, receipt_report in ((owner, applied), (receiver, opened)):
                delivery = identity.delivery_status(
                    Workspace(root), receipt_report["delivery_id"]
                )
                if delivery["status"] != "completed":
                    raise RuntimeError(
                        "Managed application delivery did not complete admission"
                    )
            # Re-serialization changes file bytes, not the authenticated message identity.
            packet = json.loads((receiver / "out.bam").read_bytes())
            (receiver / "replay.bam").write_text(
                json.dumps(packet, separators=(",", ":")), encoding="utf-8"
            )
            try:
                project_exchange.open_exchange(
                    Workspace(receiver),
                    "replay.bam",
                    "replayed",
                    peer="owner",
                    recipe="transfer.cipher-recipe.json",
                )
            except ValueError as exc:
                if "replay" not in str(exc):
                    raise
            else:
                raise RuntimeError("Managed application accepted a serialized replay")
        results[mode] = {
            "source_root": str(owner),
            "receiver_root": str(receiver),
            "export": exported,
            "outbound": sent,
            "opened": opened,
            "context": context,
            **flow,
            "returned": returned,
            "review": review,
            "apply": applied,
            "restored_execution": restored_run,
            "restored_benchmark": restored_bench,
            "local_view_unchanged": True,
            "original_bytes_preserved": True,
            "source_receipt_sha256": sha256((directory / "result.json").read_bytes()),
        }
    return {
        "workflows": results,
        "exchange_module": str(Path(project_exchange.__file__).resolve()),
        "identity_module": str(Path(identity.__file__).resolve()),
        "managed": managed,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().workspace), indent=2))
