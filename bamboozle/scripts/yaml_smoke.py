"""Run a dependent YAML-configured application through CLI, views and stdio SCM."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from bamboozle import view_import, yaml_source
from bamboozle.dictionary import Dictionary
from bamboozle.engine import Translator
from bamboozle.project_views import export_view
from bamboozle.service import OperationError
from bamboozle.utils import json_bytes, sha256
from bamboozle.workspace import Workspace

POINTERS = ["/templates/urgent", "/routes/0/message", "/routes/1/message", "/summary"]
RULE = {"format": "yaml", "pointers": POINTERS}
MAPPING = Dictionary({"alpha": "omega", "high priority": "fast lane"})
CONFIG = """\ufeff%YAML 1.2
---
# alpha is a protected comment
capacity: 3
templates:
  urgent: &urgent |- # preserve this header
    alpha for {name}
routes:
  - kind: urgent
    message: *urgent
  - kind: standard
    message: "\\u0061lpha received {name}"
summary: >-
  high
  priority alpha batches
metadata: {id: alpha, revision: 1}
""".replace("\n", "\r\n").encode("utf-8")
MODULES = {
    "app/__init__.py": "",
    "app/config.py": """from pathlib import Path
from string import Formatter
import yaml

def load(path):
    config = yaml.safe_load(Path(path).read_bytes())
    if not isinstance(config, dict) or set(config) != {"capacity", "templates", "routes", "summary", "metadata"}:
        raise ValueError("Invalid configuration fields")
    capacity = config["capacity"]
    if type(capacity) is not int or not 1 <= capacity <= 100:
        raise ValueError("Capacity must be from 1 to 100")
    routes = {}
    for route in config["routes"]:
        if set(route) != {"kind", "message"} or route["kind"] in routes:
            raise ValueError("Invalid or duplicate route")
        message = route["message"]
        if not isinstance(message, str) or {field for _, field, _, _ in Formatter().parse(message) if field is not None} != {"name"}:
            raise ValueError("Each route must contain the name field only")
        routes[route["kind"]] = message
    if not routes or not isinstance(config["summary"], str):
        raise ValueError("Missing route or summary")
    return capacity, routes, config["summary"]
""",
    "app/pipeline.py": """from .config import load

def process(path, records):
    capacity, routes, summary = load(path)
    grouped = {kind: [] for kind in routes}
    ids = set()
    for record in records:
        if set(record) != {"id", "name", "kind", "amount"} or not isinstance(record["id"], str) or record["id"] in ids:
            raise ValueError("Invalid or duplicate record")
        ids.add(record["id"])
        if record["kind"] not in routes or not isinstance(record["name"], str) or type(record["amount"]) is not int or record["amount"] < 0:
            raise ValueError("Invalid record values")
        grouped[record["kind"]].append({"id": record["id"], "amount": record["amount"], "message": routes[record["kind"]].format(name=record["name"])})
    batches = []
    for kind, items in grouped.items():
        batches.extend({"kind": kind, "items": items[index:index+capacity]} for index in range(0, len(items), capacity))
    return {"summary": summary, "records": len(records), "total": sum(record["amount"] for record in records), "batches": batches}
""",
    "app/__main__.py": """import argparse
import json
from pathlib import Path
from .pipeline import process

parser = argparse.ArgumentParser()
parser.add_argument("config")
parser.add_argument("records")
args = parser.parse_args()
print(json.dumps(process(args.config, json.loads(Path(args.records).read_bytes())), ensure_ascii=True))
""",
    "checks.py": """import json
from app.pipeline import process

count = 0
for size in range(40):
    records = [{"id": str(i), "name": "case-" + str(i), "kind": "urgent" if i % 2 else "standard", "amount": i} for i in range(size)]
    report = process("config.yaml", records)
    assert report["records"] == size and report["total"] == sum(range(size))
    assert sum(len(batch["items"]) for batch in report["batches"]) == size
    assert all(len(batch["items"]) <= 5 for batch in report["batches"])
    count += 1
for records in ([{"id": "x"}], [{"id": "x", "name": "n", "kind": "unknown", "amount": 1}], [{"id": "x", "name": "n", "kind": "urgent", "amount": True}]):
    try:
        process("config.yaml", records)
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid record accepted")
print(json.dumps({"workloads": count, "invalid_records": 3}))
""",
}


def run(workspace: Path | None = None) -> dict:
    root = (workspace or Path("yaml-workflow")).resolve()
    root.mkdir(parents=True)
    for name, content in MODULES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (root / "config.yaml").write_bytes(CONFIG)
    (root / "mapping.dictionary.json").write_bytes(json_bytes(MAPPING.document()))
    (root / "records.json").write_bytes(
        json_bytes(
            [
                {
                    "id": str(index),
                    "name": f"case-{index}",
                    "kind": "urgent" if index % 2 else "standard",
                    "amount": index,
                }
                for index in range(36)
            ]
        )
    )

    def cli(*args: str, code: int = 0) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "bamboozle", "--root", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != code:
            raise RuntimeError(
                f"YAML CLI failed: {args[0]}: {result.stdout} {result.stderr}"
            )
        return json.loads(result.stdout)

    def execute(
        directory: Path, config: str = "config.yaml", batches: int = 12
    ) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "app", config, "records.json"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        report = json.loads(result.stdout)
        assert (
            report["records"] == 36
            and report["total"] == 630
            and len(report["batches"]) == batches
        )
        return report

    baseline = execute(root)
    selection = [
        argument for pointer in POINTERS for argument in ("--yaml-pointer", pointer)
    ]
    cli(
        "encode",
        "config.yaml",
        "--dict",
        "mapping.dictionary.json",
        "--format",
        "yaml",
        *selection,
    )
    encoded = execute(root, "config_safe.yaml")
    assert json.loads(Translator(MAPPING).reverse(json.dumps(encoded))) == baseline
    cli("decode", "config_safe.yaml", "--strict", "-o", "exact.yaml")
    assert (root / "exact.yaml").read_bytes() == CONFIG
    # Strict decode retains the saved ledger; missing-metadata recovery is tested
    # separately so returned edits continue to use the original spelling context.
    returned = root / "config_safe.yaml"
    returned.write_bytes(
        returned.read_bytes()
        .replace(b"capacity: 3", b"capacity: 5")
        .replace(b"omega for", b"omega processed for")
    )
    edited = execute(root, "config_safe.yaml", 8)
    review = cli(
        "restore-edits",
        "config_safe.yaml",
        "--original",
        "config.yaml",
        "--terms",
        "translate",
        "-o",
        "restored.yaml",
    )
    cli(
        "restore-edits",
        "config_safe.yaml",
        "--original",
        "config.yaml",
        "--terms",
        "translate",
        "-o",
        "restored.yaml",
        "--apply",
        "--review-sha256",
        review["review_sha256"],
    )
    restored = execute(root, "restored.yaml", 8)
    assert json.loads(Translator(MAPPING).reverse(json.dumps(edited))) == restored

    policy: dict = {
        "schema_version": 2,
        "files": {name: {"policy": "copy"} for name in [*MODULES, "records.json"]},
        "new_files": {"configs": {"policy": "translate", "document": RULE}},
    }
    policy["files"]["config.yaml"] = {"policy": "translate", "document": RULE}
    (root / "policy.json").write_bytes(json_bytes(policy))
    view = cli(
        "view-export",
        "policy.json",
        "--output",
        "view",
        "--dict",
        "mapping.dictionary.json",
    )
    view_root = root / "view"
    (view_root / "config.yaml").write_bytes(returned.read_bytes())
    (view_root / "configs").mkdir()
    (view_root / "configs/generated.yaml").write_bytes(returned.read_bytes())
    view_result = execute(view_root, batches=8)
    view_review = cli("view-review", view["view_id"])
    (root / "config.yaml").write_bytes(CONFIG + b"# concurrent edit\r\n")
    refused = cli(
        "view-apply",
        view["view_id"],
        "--review-sha256",
        view_review["review_sha256"],
        code=1,
    )
    assert not (root / "configs/generated.yaml").exists()
    (root / "config.yaml").write_bytes(CONFIG)
    applied = cli(
        "view-apply", view["view_id"], "--review-sha256", view_review["review_sha256"]
    )
    assert execute(root, batches=8) == restored
    assert execute(root, "configs/generated.yaml", 8) == restored

    # A separate original source tree uses the same adapters through the agent's
    # actual stdio connection, including malformed edits and named app checks.
    (root / "source.agent-policy.json").write_bytes(json_bytes(policy))
    (root / "source.agent-checks.json").write_bytes(
        json_bytes(
            {
                "schema_version": 1,
                "checks": {
                    "workloads": {
                        "python": ["checks.py"],
                        "directory": ".",
                        "timeout_seconds": 20,
                        "output_bytes": 4096,
                    }
                },
            }
        )
    )
    (root / "config.yaml").write_bytes(CONFIG)

    def transport(agent: bool) -> StdioTransport:
        args = [
            "-m",
            "bamboozle",
            "--root",
            str(root),
            "serve-agent" if agent else "serve",
            "--dict",
            "mapping.dictionary.json",
        ]
        if agent:
            args += [
                "--policy",
                "source.agent-policy.json",
                "--checks",
                "source.agent-checks.json",
            ]
        return StdioTransport(command=sys.executable, args=args, cwd=str(root))

    async def operate() -> dict:
        async def call(client: Client, name: str, args: dict) -> dict:
            result = await client.call_tool(name, args)
            assert not result.is_error and isinstance(result.data, dict)
            assert result.data.get("ok") is not False
            return result.data

        async with Client(transport(False)) as operator:
            preview = await call(
                operator,
                "preview_file",
                {
                    "source": "config.yaml",
                    "output": "operator.yaml",
                    "document_format": "yaml",
                    "yaml_pointers": POINTERS,
                },
            )
            await call(operator, "apply_preview", {"plan_id": preview["plan_id"]})
            assert execute(root, "operator.yaml") == encoded
        async with Client(transport(True)) as client:
            read = await call(client, "read_safe_file", {"file_path": "config.yaml"})
            assert read["document_rules"] == RULE
            before = sha256((root / "config.yaml").read_bytes())
            bad = await client.call_tool(
                "write_safe_file",
                {
                    "file_path": "config.yaml",
                    "content": read["content"] + "capacity: 6\n",
                    "expected_sha256": read["source_sha256"],
                    "expected_context_sha256": read["context_sha256"],
                },
                raise_on_error=False,
            )
            assert (
                bad.is_error and sha256((root / "config.yaml").read_bytes()) == before
            )
            await call(
                client,
                "edit_safe_file",
                {
                    "file_path": "config.yaml",
                    "edits": [
                        {"old_text": "capacity: 3", "new_text": "capacity: 5"},
                        {"old_text": "omega for", "new_text": "omega processed for"},
                    ],
                    "expected_sha256": read["source_sha256"],
                    "expected_context_sha256": read["context_sha256"],
                    "expected_neutral_sha256": read["neutral_sha256"],
                },
            )
            updated = await call(client, "read_safe_file", {"file_path": "config.yaml"})
            await call(
                client,
                "write_safe_file",
                {"file_path": "configs/agent.yaml", "content": updated["content"]},
            )
            catalog = await call(client, "list_safe_checks", {})
            check = await call(
                client,
                "run_safe_check",
                {
                    "check_id": "workloads",
                    "expected_snapshot": catalog["checks"][0]["snapshot_sha256"],
                },
            )
            assert (
                check["status"] == "passed"
                and json.loads(check["stdout"])["workloads"] == 40
            )
        async with Client(transport(True)) as client:
            history = await call(
                client, "inspect_safe_check", {"run_id": check["run_id"]}
            )
            assert history["matches_current_source"]
        return {"check": check, "history": history, "malformed_write_refused": True}

    agent = asyncio.run(asyncio.wait_for(operate(), 120))
    assert execute(root, batches=8) == restored
    assert execute(root, "configs/agent.yaml", 8) == restored
    workspace_service = Workspace(root)
    recovery_view = export_view(
        workspace_service, "policy.json", "recovery-view", MAPPING
    )
    recovery_root = root / "recovery-view"
    changed = (
        (recovery_root / "config.yaml")
        .read_bytes()
        .replace(b"revision: 1", b"revision: 2")
    )
    (recovery_root / "config.yaml").write_bytes(changed)
    (recovery_root / "configs").mkdir()
    (recovery_root / "configs/recovered.yaml").write_bytes(changed)
    recovery_review = view_import.review_import(
        workspace_service, recovery_view["view_id"]
    )
    original_write = view_import.atomic_write

    def interrupt(path, data, *, overwrite=False):
        if path == root / "configs/recovered.yaml":
            raise OSError(
                "Injected interruption after the first YAML source publication"
            )
        original_write(path, data, overwrite=overwrite)

    view_import.atomic_write = interrupt
    failure = None
    try:
        view_import.apply_import(
            workspace_service,
            recovery_view["view_id"],
            recovery_review["review_sha256"],
        )
    except OperationError as exc:
        failure = exc.report
        assert failure["output_saved"] and not failure["receipt_saved"]
    finally:
        view_import.atomic_write = original_write
    assert failure is not None
    recovered = view_import.apply_import(
        workspace_service,
        recovery_view["view_id"],
        recovery_review["review_sha256"],
        recover=True,
    )
    assert recovered["receipt_saved"] and execute(root, batches=8) == restored
    assert execute(root, "configs/recovered.yaml", 8) == restored
    return {
        "status": "passed",
        "yaml_module": str(Path(yaml_source.__file__).resolve()),
        "source_root": str(root),
        "baseline": baseline,
        "neutral": encoded,
        "edited": edited,
        "restored": restored,
        "view_execution": view_result,
        "drift_refusal": refused,
        "view_applied": applied,
        "agent": agent,
        "interrupted_import": failure,
        "recovered_import": recovered,
        "original_sha256": sha256(CONFIG),
        "final_sha256": sha256((root / "config.yaml").read_bytes()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    print(json.dumps(run(parser.parse_args().root)))
