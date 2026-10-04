"""Execute a file-backed application through protected text CLI and stdio workflows."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from bamboozle import text_literals
from bamboozle.utils import json_bytes

RULE = {"format": "text", "protect": ["paths", "urls"]}
CONFIG = (
    '\ufefflabel=alpha task\r\ninput="fixtures/alpha records.csv"\r\n'
    "endpoint=https://alpha.example.test/api/task?label=alpha\r\n"
    "report=reports/__SCM_CASE_invalid__/alpha.json\r\ncapacity=3\r\n"
).encode("utf-8")
MODULES = {
    "app/__init__.py": "",
    "app/config.py": """from pathlib import Path
from urllib.parse import urlsplit, parse_qs

def local(root, value):
    path = (Path(root) / value).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Path escaped its application root")
    return path

def load(path):
    result = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in result:
            raise ValueError("Invalid or duplicate configuration key")
        result[key] = value.strip('"')
    if set(result) != {"label", "input", "endpoint", "report", "capacity"}:
        raise ValueError("Incomplete configuration")
    result["capacity"] = int(result["capacity"])
    if not 1 <= result["capacity"] <= 100:
        raise ValueError("Capacity must be from 1 to 100")
    endpoint = urlsplit(result["endpoint"])
    if (endpoint.scheme, endpoint.netloc, endpoint.path, parse_qs(endpoint.query)) != (
        "https", "alpha.example.test", "/api/task", {"label": ["alpha"]}
    ):
        raise ValueError("The configured dispatch endpoint changed")
    return result
""",
    "app/records.py": """import csv

def read(path):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["id", "group", "amount"]:
            raise ValueError("Invalid CSV schema")
        records, seen = [], set()
        for row in reader:
            if set(row) != {"id", "group", "amount"} or not row["id"] or row["id"] in seen:
                raise ValueError("Invalid or duplicate record")
            seen.add(row["id"])
            if row["group"] not in {"east", "west"} or not row["amount"].isascii() or not row["amount"].isdigit():
                raise ValueError("Invalid record values")
            amount = int(row["amount"])
            if amount > 1000000:
                raise ValueError("Amount exceeds limit")
            records.append({"id": row["id"], "group": row["group"], "amount": amount})
    return records
""",
    "app/pipeline.py": """import json
from .config import load, local
from .records import read

def process(root, config, output, size, factor):
    if type(size) is not int or size < 0 or type(factor) is not int or not 1 <= factor <= 4:
        raise ValueError("Invalid workload parameters")
    settings = load(local(root, config))
    rows = read(local(root, settings["input"]))[:size]
    capacity = settings["capacity"]
    result = {
        "label": settings["label"], "endpoint": settings["endpoint"],
        "records": len(rows), "total": sum(row["amount"] * factor for row in rows),
        "batches": [rows[start:start+capacity] for start in range(0, len(rows), capacity)],
    }
    destination = local(output, settings["report"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    if json.loads(destination.read_bytes()) != result:
        raise RuntimeError("Published report did not round-trip")
    return result
""",
    "app/__main__.py": """import argparse
import json
from .pipeline import process

parser = argparse.ArgumentParser()
parser.add_argument("config")
parser.add_argument("output")
parser.add_argument("--size", type=int, default=40)
parser.add_argument("--factor", type=int, default=1)
args = parser.parse_args()
print(json.dumps(process(".", args.config, args.output, args.size, args.factor)))
""",
    "checks.py": """import json
import shutil
import sys
import tempfile
from pathlib import Path
from app.pipeline import process

output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
executions = 0
for size in range(40):
    for factor in range(1, 5):
        report = process(".", "settings.txt", output, size, factor)
        assert report["records"] == size and report["total"] == sum(range(size)) * factor
        assert sum(len(batch) for batch in report["batches"]) == size
        assert all(len(batch) <= 5 for batch in report["batches"])
        assert report["stats"] == {group: sum(i for i in range(size) if ("east" if i % 2 else "west") == group) * factor for group in ("east", "west")}
        executions += 1
with tempfile.TemporaryDirectory(dir=output) as scratch:
    root = Path(scratch)
    (root / "fixtures").mkdir()
    shutil.copyfile("settings.txt", root / "settings.txt")
    invalid = ["id,group,amount\\na,east,1\\na,west,2\\n", "id,group,amount\\na,north,1\\n", "id,group,amount\\na,east,-1\\n", "wrong,header\\na,1\\n"]
    for content in invalid:
        (root / "fixtures/alpha records.csv").write_text(content)
        try:
            process(root, "settings.txt", output, 40, 1)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid dataset accepted")
print(json.dumps({"application_cases": executions, "invalid_datasets": len(invalid)}))
""",
}
STATS = """def summarize(rows, factor):
    totals = {"east": 0, "west": 0}
    for row in rows:
        totals[row["group"]] += row["amount"] * factor
    return totals
"""
ENHANCED = (
    MODULES["app/pipeline.py"]
    .replace(
        "from .records import read",
        "from .records import read\nfrom .stats import summarize",
    )
    .replace(
        "    destination = local",
        '    result["stats"] = summarize(rows, factor)\n    destination = local',
    )
)


def run(workspace: Path | None = None) -> dict:
    roots = (workspace or Path("literal-workflow")).resolve()
    roots.mkdir(parents=True)
    results = {}

    def execute(
        root: Path,
        output: Path,
        *,
        config: str = "settings.txt",
        enhanced: bool = False,
    ) -> dict:
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "app", config, str(output)],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        report = json.loads(completed.stdout)
        assert report["records"] == 40 and report["total"] == 780
        assert len(report["batches"]) == (8 if enhanced else 14)
        assert ("stats" in report) == enhanced
        assert (
            json.loads(
                (output / "reports/__SCM_CASE_invalid__/alpha.json").read_bytes()
            )
            == report
        )
        return report

    def benchmark(root: Path, output: Path) -> dict:
        completed = subprocess.run(
            [sys.executable, "-B", "checks.py", str(output)],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        result = json.loads(completed.stdout)
        assert result == {"application_cases": 160, "invalid_datasets": 4}
        return result

    for mode in ("cli", "mcp", "agent"):
        owner, output = roots / mode / "source", roots / mode / "results"
        owner.mkdir(parents=True)
        output.mkdir()
        for name, content in MODULES.items():
            path = owner / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (owner / "fixtures").mkdir()
        (owner / "fixtures/alpha records.csv").write_text(
            "id,group,amount\n"
            + "".join(f"{i},{'east' if i % 2 else 'west'},{i}\n" for i in range(40)),
            encoding="utf-8",
        )
        (owner / "settings.txt").write_bytes(CONFIG)
        (owner / "mapping.dictionary.json").write_bytes(
            json_bytes({"alpha": "omega", "task": "stage"})
        )
        policy: dict = {
            "schema_version": 2,
            "files": {
                name: {"policy": "copy"}
                for name in [*MODULES, "fixtures/alpha records.csv"]
            },
            "new_files": {
                "app": "copy",
                "configs": {"policy": "translate", "document": RULE},
            },
        }
        policy["files"]["settings.txt"] = {"policy": "translate", "document": RULE}
        (owner / "source.agent-policy.json").write_bytes(json_bytes(policy))
        baseline = execute(owner, output / "baseline")

        def cli(*args: str, owner: Path = owner) -> dict:
            completed = subprocess.run(
                [sys.executable, "-m", "bamboozle", "--root", str(owner), *args],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            result = json.loads(completed.stdout)
            assert result.get("ok") is not False and result.get("status") != "error"
            return result

        def edit(view: Path, output: Path = output) -> dict:
            config = view / "settings.txt"
            neutral = config.read_bytes()
            assert b"label=omega stage" in neutral
            assert neutral == CONFIG.replace(b"label=alpha task", b"label=omega stage")
            execute(view, output / "neutral-before")
            config.write_bytes(neutral.replace(b"capacity=3", b"capacity=5"))
            (view / "app/stats.py").write_text(STATS)
            (view / "app/pipeline.py").write_text(ENHANCED)
            (view / "configs").mkdir()
            (view / "configs/generated.txt").write_bytes(config.read_bytes())
            result = execute(view, output / "neutral", enhanced=True)
            assert (
                execute(
                    view,
                    output / "generated-neutral",
                    config="configs/generated.txt",
                    enhanced=True,
                )
                == result
            )
            return {
                "execution": result,
                "benchmark": benchmark(view, output / "neutral-cases"),
            }

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

                async def call(tool_name: str, arguments: dict) -> dict:
                    result = await client.call_tool(tool_name, arguments)
                    assert (
                        not result.is_error
                        and isinstance(result.data, dict)
                        and result.data.get("ok") is not False
                    )
                    return result.data

                if mode == "agent":
                    read = await call("read_safe_file", {"file_path": "settings.txt"})
                    assert read["document_rules"] == RULE
                    edited = read["content"].replace("capacity=3", "capacity=5")
                    await call(
                        "write_safe_file",
                        {
                            "file_path": "settings.txt",
                            "content": edited,
                            "expected_sha256": read["source_sha256"],
                            "expected_context_sha256": read["context_sha256"],
                        },
                    )
                    await call(
                        "write_safe_file",
                        {"file_path": "configs/generated.txt", "content": edited},
                    )
                    await call(
                        "write_safe_file",
                        {"file_path": "app/stats.py", "content": STATS},
                    )
                    read = await call(
                        "read_safe_file", {"file_path": "app/pipeline.py"}
                    )
                    await call(
                        "write_safe_file",
                        {
                            "file_path": "app/pipeline.py",
                            "content": ENHANCED,
                            "expected_sha256": read["source_sha256"],
                            "expected_context_sha256": read["context_sha256"],
                        },
                    )
                    return {"encoded_reads_decoded_writes": True}
                preview = await call(
                    "preview_file",
                    {
                        "source": "settings.txt",
                        "output": "manual.txt",
                        "document_format": "text",
                        "protect": ["paths", "urls"],
                    },
                )
                await call("apply_preview", {"plan_id": preview["plan_id"]})
                execute(owner, output / "manual", config="manual.txt")
                reverse = await call(
                    "preview_file",
                    {
                        "source": "manual.txt",
                        "output": "exact.txt",
                        "reverse": True,
                        "strict": True,
                    },
                )
                await call("apply_preview", {"plan_id": reverse["plan_id"]})
                assert (owner / "exact.txt").read_bytes() == CONFIG
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
                "settings.txt",
                "-o",
                "manual.txt",
                "--dict",
                "mapping.dictionary.json",
                "--format",
                "text",
                "--protect",
                "paths",
                "--protect",
                "urls",
            )
            execute(owner, output / "manual", config="manual.txt")
            cli("decode", "manual.txt", "--strict", "-o", "exact.txt")
            assert (owner / "exact.txt").read_bytes() == CONFIG
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
        restored = execute(owner, output / "restored", enhanced=True)
        assert restored["label"] == baseline["label"]
        assert (
            execute(
                owner,
                output / "generated-restored",
                config="configs/generated.txt",
                enhanced=True,
            )
            == restored
        )
        assert (owner / "settings.txt").read_bytes() == CONFIG.replace(
            b"capacity=3", b"capacity=5"
        )
        if "execution" in flow:
            assert {**flow["execution"], "label": restored["label"]} == restored
        results[mode] = {
            **flow,
            "restored_execution": restored,
            "restored_benchmark": benchmark(owner, output / "restored-cases"),
        }
    return {
        "status": "passed",
        "text_literals_module": text_literals.__file__,
        "application_cases": 160,
        "workflows": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    print(json.dumps(run(parser.parse_args().workspace), indent=2))
