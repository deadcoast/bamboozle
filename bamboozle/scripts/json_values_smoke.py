"""Execute a nested JSON-configured batching application through CLI and stdio."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from bamboozle import formats, view_import
from bamboozle.dictionary import Dictionary
from bamboozle.project_views import export_view
from bamboozle.service import OperationError
from bamboozle.utils import atomic_write, json_bytes
from bamboozle.workspace import Workspace

RULE = {"format": "json", "values_only": True}
MAPPING = Dictionary({"alpha": "omega", "task": "stage"})
CONFIG = (
    '\ufeff{\r\n "alpha": {"capacity":3, "routes":['
    '{"code":"alpha","message":"\\u0061lpha task for {name}"},'
    '{"code":"task","message":"task received {name}"}]},\r\n'
    ' "footer":["alpha", "omega", "__SCM_LIT_omega__"],'
    ' "threshold":1e+02, "enabled":true, "optional":null\r\n}\r\n'
).encode()
MODULES = {
    "app/__init__.py": "",
    "app/config.py": """import json
from pathlib import Path
from string import Formatter

def load(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if set(config) != {"alpha", "footer", "threshold", "enabled", "optional"} or config["enabled"] is not True:
        raise ValueError("Invalid configuration keys or enable flag")
    capacity = config["alpha"]["capacity"]
    if type(capacity) is not int or not 1 <= capacity <= 100:
        raise ValueError("Invalid capacity")
    routes = {}
    for route in config["alpha"]["routes"]:
        if set(route) != {"code", "message"} or not isinstance(route["code"], str) or route["code"] in routes:
            raise ValueError("Invalid or duplicate route")
        if not isinstance(route["message"], str) or {field for _,field,_,_ in Formatter().parse(route["message"]) if field is not None} != {"name"}:
            raise ValueError("Invalid message placeholders")
        routes[route["code"]] = route["message"]
    return capacity, routes, config["footer"]
""",
    "app/pipeline.py": """import json
from pathlib import Path
from .config import load

def process(root, config, records, size, factor):
    if type(size) is not int or size < 0 or type(factor) is not int or not 1 <= factor <= 4:
        raise ValueError("Invalid workload parameters")
    capacity, routes, footer = load(Path(root) / config)
    rows = json.loads((Path(root) / records).read_bytes())
    if not isinstance(rows, list):
        raise ValueError("Expected a record array")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "kind", "name", "amount"} or not isinstance(row["id"], str) or row["id"] in seen:
            raise ValueError("Invalid or duplicate record")
        seen.add(row["id"])
        if row["kind"] not in routes or not isinstance(row["name"], str) or type(row["amount"]) is not int or row["amount"] < 0:
            raise ValueError("Invalid record values")
    rows = rows[:size]
    grouped = {code: [] for code in routes}
    for row in rows:
        grouped[row["kind"]].append({"id":row["id"], "amount":row["amount"] * factor, "message":routes[row["kind"]].format(name=row["name"])})
    batches = [{"kind":code, "items":items[index:index+capacity]} for code,items in grouped.items() for index in range(0,len(items),capacity)]
    report = {"records":len(rows), "total":sum(row["amount"] for row in rows)*factor, "batches":batches, "footer":footer}
    return report
""",
    "app/__main__.py": """import argparse
import json
from pathlib import Path
from .pipeline import process

parser = argparse.ArgumentParser()
parser.add_argument("config")
parser.add_argument("records")
parser.add_argument("output")
args = parser.parse_args()
report = process(".", args.config, args.records, 10000, 1)
destination = Path(args.output)
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(report), encoding="utf-8")
assert json.loads(destination.read_bytes()) == report
print(json.dumps(report))
""",
    "checks.py": """import json
import shutil
import sys
import tempfile
from pathlib import Path
from app.pipeline import process

cases = 0
for size in (0,1,2,3,4,7,8,14,99,100,299,300,301,302):
    for factor in range(1,5):
        report = process(".", "config.json", "records.json", size, factor)
        used = min(size,301)
        assert report["records"] == used and report["total"] == sum(range(used))*factor
        assert sum(len(batch["items"]) for batch in report["batches"]) == used
        assert all(len(batch["items"]) <= 7 for batch in report["batches"])
        assert report["summary"]["total"] == report["total"]
        assert report["summary"]["batches"] == len(report["batches"])
        cases += 1
output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=output) as scratch:
    root = Path(scratch)
    shutil.copyfile("config.json", root / "config.json")
    valid = json.loads(Path("records.json").read_bytes())[0]
    invalid = [[valid,valid], [dict(valid,kind="missing")], [dict(valid,amount=True)], [{"id":"incomplete"}]]
    for rows in invalid:
        (root / "records.json").write_text(json.dumps(rows))
        try:
            process(root,"config.json","records.json",10,1)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid records accepted")
print(json.dumps({"workloads":cases,"invalid_datasets":len(invalid)}))
""",
}
SUMMARY = """def summarize(batches):
    counts = {}
    total = 0
    for batch in batches:
        counts[batch["kind"]] = counts.get(batch["kind"],0) + len(batch["items"])
        total += sum(item["amount"] for item in batch["items"])
    return {"counts":counts, "total":total, "batches":len(batches)}
"""
ENHANCED = (
    MODULES["app/pipeline.py"]
    .replace(
        "from .config import load",
        "from .config import load\nfrom .summary import summarize",
    )
    .replace(
        "    return report",
        '    report["summary"] = summarize(batches)\n    return report',
    )
)


def run(workspace: Path | None = None) -> dict:
    roots = (workspace or Path("json-values-workflow")).resolve()
    roots.mkdir(parents=True)
    results = {}

    def execute(
        root: Path,
        output: Path,
        *,
        config="config.json",
        records="records.json",
        enhanced=False,
    ) -> dict:
        process = subprocess.run(
            [sys.executable, "-B", "-m", "app", config, records, str(output)],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        result = json.loads(process.stdout)
        assert result["records"] == (301 if enhanced else 300)
        assert result["total"] == (45150 if enhanced else 44850)
        assert len(result["batches"]) == (45 if enhanced else 100)
        assert ("summary" in result) == enhanced
        assert json.loads(output.read_bytes()) == result
        return result

    def benchmark(root: Path, output: Path) -> dict:
        process = subprocess.run(
            [sys.executable, "-B", "checks.py", str(output)],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        result = json.loads(process.stdout)
        assert result == {"workloads": 56, "invalid_datasets": 4}
        return result

    def changed(content: str) -> str:
        return content.replace('"capacity":3', '"capacity":7').replace(
            '"routes":[',
            '"routes":[{"code":"new","message":"omega stage audit {name}"},',
        )

    for mode in ("cli", "mcp", "agent"):
        owner, output = roots / mode / "source", roots / mode / "results"
        owner.mkdir(parents=True)
        output.mkdir()
        for name, content in MODULES.items():
            path = owner / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (owner / "config.json").write_bytes(CONFIG)
        records = [
            {
                "id": str(i),
                "kind": "alpha" if i % 2 else "task",
                "name": f"name-{i}",
                "amount": i,
            }
            for i in range(300)
        ]
        (owner / "records.json").write_bytes(json_bytes(records))
        (owner / "mapping.dictionary.json").write_bytes(json_bytes(MAPPING.document()))
        policy: dict = {
            "schema_version": 2,
            "files": {name: {"policy": "copy"} for name in MODULES},
            "new_files": {
                "app": "copy",
                "generated": {"policy": "translate", "document": RULE},
            },
        }
        for name in ("config.json", "records.json"):
            policy["files"][name] = {"policy": "translate", "document": RULE}
        (owner / "source.agent-policy.json").write_bytes(json_bytes(policy))
        execute(owner, output / "baseline.json")

        def cli(*args: str, owner: Path = owner) -> dict:
            process = subprocess.run(
                [sys.executable, "-m", "bamboozle", "--root", str(owner), *args],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            result = json.loads(process.stdout)
            assert result.get("ok") is not False and result.get("status") != "error"
            return result

        def edit(view: Path, output: Path = output) -> dict:
            execute(view, output / "neutral-before.json")
            content = (view / "config.json").read_bytes().decode()
            assert (
                '"alpha":' in content
                and '"message":"omega stage for {name}"' in content
            )
            (view / "config.json").write_bytes(changed(content).encode())
            rows = json.loads((view / "records.json").read_bytes())
            rows.append(
                {"id": "new-300", "kind": "new", "name": "omega", "amount": 300}
            )
            (view / "records.json").write_bytes(json_bytes(rows))
            (view / "app/summary.py").write_text(SUMMARY)
            (view / "app/pipeline.py").write_text(ENHANCED)
            (view / "generated").mkdir()
            (view / "generated/config.json").write_bytes(
                (view / "config.json").read_bytes()
            )
            result = execute(view, output / "neutral.json", enhanced=True)
            assert (
                execute(
                    view,
                    output / "neutral-generated.json",
                    config="generated/config.json",
                    enhanced=True,
                )
                == result
            )
            return {"neutral_benchmark": benchmark(view, output / "neutral-cases")}

        async def operate(
            owner: Path = owner, output: Path = output, mode: str = mode, edit=edit
        ) -> dict:
            args = [
                "-m",
                "bamboozle",
                "--root",
                str(owner),
                "serve-agent" if mode == "agent" else "serve",
                "--dict",
                "mapping.dictionary.json",
            ]
            if mode == "agent":
                args += ["--policy", "source.agent-policy.json"]
            async with Client(
                StdioTransport(command=sys.executable, args=args, cwd=str(owner))
            ) as client:

                async def call(tool: str, arguments: dict) -> dict:
                    response = await client.call_tool(tool, arguments)
                    assert (
                        not response.is_error
                        and isinstance(response.data, dict)
                        and response.data.get("ok") is not False
                    )
                    return response.data

                if mode == "agent":
                    config = await call("read_safe_file", {"file_path": "config.json"})
                    assert config["document_rules"] == RULE
                    content = changed(config["content"])
                    await call(
                        "write_safe_file",
                        {
                            "file_path": "config.json",
                            "content": content,
                            "expected_sha256": config["source_sha256"],
                            "expected_context_sha256": config["context_sha256"],
                        },
                    )
                    await call(
                        "write_safe_file",
                        {"file_path": "generated/config.json", "content": content},
                    )
                    rows = await call("read_safe_file", {"file_path": "records.json"})
                    data = json.loads(rows["content"])
                    data.append(
                        {"id": "new-300", "kind": "new", "name": "omega", "amount": 300}
                    )
                    await call(
                        "write_safe_file",
                        {
                            "file_path": "records.json",
                            "content": json.dumps(data),
                            "expected_sha256": rows["source_sha256"],
                            "expected_context_sha256": rows["context_sha256"],
                        },
                    )
                    await call(
                        "write_safe_file",
                        {"file_path": "app/summary.py", "content": SUMMARY},
                    )
                    source = await call(
                        "read_safe_file", {"file_path": "app/pipeline.py"}
                    )
                    await call(
                        "write_safe_file",
                        {
                            "file_path": "app/pipeline.py",
                            "content": ENHANCED,
                            "expected_sha256": source["source_sha256"],
                            "expected_context_sha256": source["context_sha256"],
                        },
                    )
                    return {"encoded_reads_decoded_writes": True}
                preview = await call(
                    "preview_file",
                    {
                        "source": "config.json",
                        "output": "manual.json",
                        "json_values_only": True,
                    },
                )
                await call("apply_preview", {"plan_id": preview["plan_id"]})
                preview = await call(
                    "preview_file",
                    {
                        "source": "manual.json",
                        "output": "exact.json",
                        "reverse": True,
                        "strict": True,
                    },
                )
                await call("apply_preview", {"plan_id": preview["plan_id"]})
                assert (owner / "exact.json").read_bytes() == CONFIG
                exported = await call(
                    "export_project_view",
                    {"config": "source.agent-policy.json", "output": "view"},
                )
                flow = edit(owner / "view")
                review = await call(
                    "review_project_view", {"view_id": exported["view_id"]}
                )
                await call(
                    "apply_project_view",
                    {
                        "view_id": exported["view_id"],
                        "review_sha256": review["review_sha256"],
                    },
                )
                return flow

        if mode == "cli":
            cli(
                "encode",
                "config.json",
                "-o",
                "manual.json",
                "--dict",
                "mapping.dictionary.json",
                "--json-values-only",
            )
            cli("decode", "manual.json", "--strict", "-o", "exact.json")
            assert (owner / "exact.json").read_bytes() == CONFIG
            exported = cli(
                "view-export",
                "source.agent-policy.json",
                "--output",
                "view",
                "--dict",
                "mapping.dictionary.json",
            )
            flow = edit(owner / "view")
            review = cli("view-review", exported["view_id"])
            cli(
                "view-apply",
                exported["view_id"],
                "--review-sha256",
                review["review_sha256"],
            )
        else:
            flow = asyncio.run(asyncio.wait_for(operate(), 90))
        restored = execute(owner, output / "restored.json", enhanced=True)
        assert (
            execute(
                owner,
                output / "generated.json",
                config="generated/config.json",
                enhanced=True,
            )
            == restored
        )
        assert restored["footer"] == ["alpha", "omega", "__SCM_LIT_omega__"]
        assert (
            b'"message":"\\u0061lpha task for {name}"'
            in (owner / "config.json").read_bytes()
        )
        assert b'"threshold":1e+02' in (owner / "config.json").read_bytes()
        assert json.loads((owner / "records.json").read_bytes())[-1]["name"] == "alpha"
        flow["restored_benchmark"] = benchmark(owner, output / "restored-cases")

        # Retained rules and spelling context also survive partial publication.
        workspace_service = Workspace(owner)
        recovery = export_view(
            workspace_service, "source.agent-policy.json", "recovery-view", MAPPING
        )
        tree = owner / "recovery-view"
        path = tree / "config.json"
        path.write_bytes(
            path.read_bytes().replace(b'"optional":null', b'"optional":false')
        )
        path = tree / "records.json"
        path.write_bytes(path.read_bytes().replace(b'"name-0"', b'"name\\u002d0"'))
        reviewed = view_import.review_import(workspace_service, recovery["view_id"])
        failure = None

        def interrupt(path, data, *, overwrite=False, owner: Path = owner):
            if path == owner / "records.json":
                raise OSError("Injected interruption after first JSON publication")
            atomic_write(path, data, overwrite=overwrite)

        view_import.atomic_write = interrupt
        try:
            view_import.apply_import(
                workspace_service, recovery["view_id"], reviewed["review_sha256"]
            )
        except OperationError as exc:
            failure = exc.report
            assert failure["output_saved"] and not failure["receipt_saved"]
        finally:
            view_import.atomic_write = atomic_write
        assert failure is not None
        resumed = view_import.apply_import(
            workspace_service,
            recovery["view_id"],
            reviewed["review_sha256"],
            recover=True,
        )
        assert resumed["receipt_saved"]
        assert execute(owner, output / "recovered.json", enhanced=True) == restored
        assert (
            execute(
                owner,
                output / "recovered-generated.json",
                config="generated/config.json",
                enhanced=True,
            )
            == restored
        )
        results[mode] = {
            **flow,
            "recovery": resumed,
            "records": restored["records"],
            "total": restored["total"],
        }
    return {
        "status": "passed",
        "formats_module": formats.__file__,
        "workflows": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    print(json.dumps(run(parser.parse_args().workspace), indent=2))
