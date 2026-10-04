"""Complete CLI/stdio applications and peer lifecycle in isolated operator roots."""

from __future__ import annotations

import argparse
import asyncio
import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from bamboozle import identity
from bamboozle.tessera import create_key
from bamboozle.utils import sha256
from bamboozle.workspace import Workspace


def run(workspace: Path | None = None) -> dict[str, Any]:
    root = (workspace or Path("identity-workflow")).resolve()
    root.mkdir(parents=True)
    projects = runpy.run_path(str(Path(__file__).with_name("exchange_smoke.py")))[
        "run"
    ](root / "projects", managed=True)
    outcomes = {}
    for mode in ("cli", "mcp"):
        left, right, outsider = [
            root / mode / name for name in ("owner", "receiver", "outsider")
        ]
        for directory in (left, right, outsider):
            directory.mkdir(parents=True)
        for name in ("first.key", "second.key"):
            create_key(Workspace(left), name)
            (right / name).write_bytes((left / name).read_bytes())
        (left / "data.bin").write_bytes(bytes(range(256)) * 1024)

        async def lifecycle(
            left: Path = left,
            right: Path = right,
            outsider: Path = outsider,
            mode: str = mode,
        ) -> dict:
            def transport(directory: Path) -> StdioTransport:
                return StdioTransport(
                    command=sys.executable,
                    args=["-m", "bamboozle", "--root", str(directory), "serve"],
                    cwd=str(directory),
                )

            async with (
                Client(transport(left)) as first,
                Client(transport(right)) as second,
            ):

                async def invoke(
                    directory: Path,
                    tool: str,
                    arguments: dict,
                    command: list[str],
                    *,
                    refused: bool = False,
                ) -> dict:
                    if mode == "cli":
                        process = await asyncio.to_thread(
                            subprocess.run,
                            [
                                sys.executable,
                                "-m",
                                "bamboozle",
                                "--root",
                                str(directory),
                                *command,
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=30,
                        )
                        data = json.loads(process.stdout)
                        failed = process.returncode != 0
                    else:
                        result = await (
                            first if directory == left else second
                        ).call_tool(tool, arguments, raise_on_error=False)
                        data = (
                            result.data
                            if isinstance(result.data, dict)
                            else {"error": str(result.content)}
                        )
                        failed = (
                            result.is_error
                            or data.get("ok") is False
                            or data.get("status") == "error"
                        )
                    if bool(failed) != refused:
                        raise RuntimeError(
                            f"Unexpected lifecycle outcome: {tool}: {data}"
                        )
                    return data

                for directory, name, peer in (
                    (left, "owner", "receiver"),
                    (right, "receiver", "owner"),
                ):
                    await invoke(
                        directory,
                        "initialize_identity",
                        {"name": name},
                        ["identity-init", name],
                    )
                    await invoke(
                        directory,
                        "change_recipient",
                        {"peer": peer, "action": "register", "key": "first.key"},
                        ["recipient-register", peer, "--key", "first.key"],
                    )
                for packet in ("inflight.bam", "revoked.bam"):
                    await invoke(
                        left,
                        "seal_file",
                        {"source": "data.bin", "output": packet, "peer": "receiver"},
                        ["seal", "data.bin", "-o", packet, "--peer", "receiver"],
                    )
                    (right / packet).write_bytes((left / packet).read_bytes())
                for directory, peer in ((left, "receiver"), (right, "owner")):
                    registry = await invoke(
                        directory, "recipient_registry", {}, ["identity-status"]
                    )
                    await invoke(
                        directory,
                        "change_recipient",
                        {
                            "peer": peer,
                            "action": "rotate",
                            "key": "second.key",
                            "expected_sha256": registry["registry_sha256"],
                        },
                        [
                            "recipient-rotate",
                            peer,
                            "--key",
                            "second.key",
                            "--expected-sha256",
                            registry["registry_sha256"],
                        ],
                    )
                opened = await invoke(
                    right,
                    "open_file",
                    {
                        "source": "inflight.bam",
                        "output": "received.bin",
                        "peer": "owner",
                    },
                    ["open", "inflight.bam", "-o", "received.bin", "--peer", "owner"],
                )
                if (right / "received.bin").read_bytes() != (
                    left / "data.bin"
                ).read_bytes():
                    raise RuntimeError(
                        "Retired in-flight key changed binary file bytes"
                    )
                receipt = await invoke(
                    right,
                    "inspect_delivery",
                    {"delivery_id": opened["delivery_id"]},
                    ["delivery-status", opened["delivery_id"]],
                )
                if receipt["status"] != "completed":
                    raise RuntimeError("File delivery has no completed admission")
                await invoke(
                    right,
                    "open_file",
                    {"source": "inflight.bam", "output": "replay.bin", "peer": "owner"},
                    ["open", "inflight.bam", "-o", "replay.bin", "--peer", "owner"],
                    refused=True,
                )
                registry = await invoke(
                    right, "recipient_registry", {}, ["identity-status"]
                )
                key_id = sha256((right / "first.key").read_bytes())
                await invoke(
                    right,
                    "change_recipient",
                    {
                        "peer": "owner",
                        "action": "revoke",
                        "key_id": key_id,
                        "expected_sha256": registry["registry_sha256"],
                    },
                    [
                        "recipient-revoke",
                        "owner",
                        "--key-id",
                        key_id,
                        "--expected-sha256",
                        registry["registry_sha256"],
                    ],
                )
                await invoke(
                    right,
                    "open_file",
                    {"source": "revoked.bam", "output": "revoked.bin", "peer": "owner"},
                    ["open", "revoked.bam", "-o", "revoked.bin", "--peer", "owner"],
                    refused=True,
                )
                await invoke(
                    left,
                    "seal_file",
                    {"source": "data.bin", "output": "rotated.bam", "peer": "receiver"},
                    ["seal", "data.bin", "-o", "rotated.bam", "--peer", "receiver"],
                )
                (right / "rotated.bam").write_bytes((left / "rotated.bam").read_bytes())
                await invoke(
                    right,
                    "open_file",
                    {"source": "rotated.bam", "output": "rotated.bin", "peer": "owner"},
                    ["open", "rotated.bam", "-o", "rotated.bin", "--peer", "owner"],
                )
                if (right / "rotated.bin").read_bytes() != (
                    left / "data.bin"
                ).read_bytes():
                    raise RuntimeError("Rotated delivery changed payload")
                # A third local identity cannot accept another recipient's packet,
                # even when explicitly provisioned with the same test secret.
                identity.initialize(Workspace(outsider), "outsider")
                (outsider / "test.key").write_bytes((left / "second.key").read_bytes())
                identity.change_peer(
                    Workspace(outsider), "owner", action="register", key="test.key"
                )
                try:
                    identity.authenticate(
                        Workspace(outsider),
                        (left / "rotated.bam").read_bytes(),
                        "owner",
                        "file",
                    )
                except ValueError as exc:
                    if "mismatch" not in str(exc):
                        raise
                else:
                    raise RuntimeError(
                        "Another identity accepted a misaddressed delivery"
                    )
                registry = await invoke(
                    right, "recipient_registry", {}, ["identity-status"]
                )
                encoded_report = json.dumps(registry).encode()
                if any(
                    (left / name).read_bytes() in encoded_report
                    for name in ("first.key", "second.key")
                ):
                    raise RuntimeError("Registry report exposed key bytes")
                if (right / "replay.bin").exists() or (right / "revoked.bin").exists():
                    raise RuntimeError("Refused delivery published output")
                return {
                    "binary_bytes": 262144,
                    "retired_inflight_received": True,
                    "rotation_received": True,
                    "replay_refused": True,
                    "revocation_refused": True,
                    "isolated_recipient_refused": True,
                    "registry_sha256": registry["registry_sha256"],
                    "delivery_id": opened["delivery_id"],
                }

        outcomes[mode] = asyncio.run(asyncio.wait_for(lifecycle(), timeout=120))
    return {
        "identity_module": str(Path(identity.__file__).resolve()),
        "applications": projects,
        "lifecycle": outcomes,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().workspace), indent=2))
