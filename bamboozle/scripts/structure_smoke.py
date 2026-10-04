"""Qualify profile-governed source development with an executable application."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from bamboozle import structure, view_import
from bamboozle.dictionary import Dictionary
from bamboozle.service import OperationError
from bamboozle.utils import json_bytes, sha256
from bamboozle.workspace import Workspace

MODULES = {
    "app/__init__.py": "",
    "app/core.py": """MAX_AMOUNT = 1000000

def alpha(records, multiplier):
    if type(multiplier) is not int or not 1 <= multiplier <= 10:
        raise ValueError("Invalid multiplier")
    seen = set()
    groups = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {"id", "group", "amount"}:
            raise ValueError("Invalid record fields")
        identity, group, amount = record["id"], record["group"], record["amount"]
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("Invalid or duplicate identity")
        if not isinstance(group, str) or not group or type(amount) is not int or not 0 <= amount <= MAX_AMOUNT:
            raise ValueError("Invalid group or amount")
        seen.add(identity)
        groups[group] = groups.get(group, 0) + amount * multiplier
    return {"records": len(seen), "total": sum(groups.values()), "groups": groups}
""",
    "app/__main__.py": """import argparse
import json
from pathlib import Path
from .core import alpha

parser = argparse.ArgumentParser()
parser.add_argument("records")
parser.add_argument("--multiplier", type=int, default=2)
args = parser.parse_args()
print(json.dumps(alpha(json.loads(Path(args.records).read_bytes()), args.multiplier)))
""",
    "tests/test_core.py": """import unittest
from app.core import alpha

class Processing(unittest.TestCase):
    def test_workloads(self):
        for size in range(80):
            for multiplier in (1, 2, 10):
                rows = [{"id": str(i), "group": "g" + str(i % 3), "amount": i} for i in range(size)]
                result = alpha(rows, multiplier)
                self.assertEqual(result["records"], size)
                self.assertEqual(result["total"], sum(range(size)) * multiplier)
                self.assertEqual(result["groups"], {key: sum(r["amount"] for r in rows if r["group"] == key) * multiplier for key in {r["group"] for r in rows}})

    def test_bad_records(self):
        cases = [[{}], [{"id": "x", "group": "g", "amount": True}], [{"id": "x", "group": "g", "amount": -1}], [{"id": "x", "group": "g", "amount": 1}] * 2]
        for rows in cases:
            with self.assertRaises(ValueError):
                alpha(rows, 2)
        for multiplier in (0, 11, True):
            with self.assertRaises(ValueError):
                alpha([], multiplier)
""",
}

SUMMARY = """from .core import omega

def rank(records, multiplier):
    result = omega(records, multiplier)
    result["ranking"] = sorted(result["groups"], key=lambda key: (-result["groups"][key], key))
    return result
"""
SUMMARY_TEST = """import unittest
from app.summary import rank

class Ranking(unittest.TestCase):
    def test_ranked_workloads(self):
        for size in range(60):
            rows = [{"id": str(i), "group": "g" + str(i % 4), "amount": i} for i in range(size)]
            report = rank(rows, 2)
            self.assertEqual(report["total"], sum(range(size)) * 2)
            self.assertEqual(report["ranking"], sorted(report["groups"], key=lambda key: (-report["groups"][key], key)))

    def test_ties_and_empty(self):
        rows = [{"id": key, "group": key, "amount": 4} for key in ("b", "a")]
        self.assertEqual(rank(rows, 2)["ranking"], ["a", "b"])
        self.assertEqual(rank([], 2)["ranking"], [])

    def test_dependency_rejects_invalid(self):
        with self.assertRaises(ValueError):
            rank([{"id": "x", "group": "g", "amount": True}], 2)
"""


def run(workspace: Path | None = None) -> dict:
    root = (workspace or Path("structure-workflow")).resolve()
    root.mkdir(parents=True)

    def put(name: str, raw: bytes, directory: Path = root) -> None:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    for name, content in MODULES.items():
        put(name, content.encode())
    put(
        "records.json",
        json_bytes(
            [{"id": str(i), "group": "g" + str(i % 3), "amount": i} for i in range(30)]
        ),
    )
    rule = {
        "suffixes": [".py"],
        "formats": {".py": "python"},
        "max_files": 64,
        "max_file_bytes": 65536,
        "max_total_bytes": 1048576,
    }
    profile = {
        "kind": "bamboozle.structure",
        "schema_version": 1,
        "name": "group-processor",
        "roots": {"app": rule, "tests": rule},
        "required": {
            "app/__init__.py": "file",
            "app/__main__.py": "file",
            "tests/test_core.py": "file",
        },
        "companions": [
            {
                "source": "app",
                "source_suffix": ".py",
                "target": "tests",
                "target_suffix": ".py",
                "target_prefix": "test_",
                "exclude_names": ["__init__.py", "__main__.py"],
            }
        ],
        "exclude": [],
    }
    put("draft.structure-profile.json", json_bytes(profile))
    put(
        "mapping.dictionary.json", json_bytes(Dictionary({"alpha": "omega"}).document())
    )
    policy: dict[str, Any] = {
        "schema_version": 2,
        "files": {name: {"policy": "translate"} for name in MODULES},
        "new_files": {
            "app": {"policy": "translate", "document": {"format": "text"}},
            "tests": {"policy": "translate", "document": {"format": "text"}},
        },
    }
    policy["files"]["records.json"] = {"policy": "copy"}
    put("source.agent-policy.json", json_bytes(policy))

    def cli(*args: str, code: int = 0) -> dict:
        completed = subprocess.run(
            [sys.executable, "-m", "bamboozle", "--root", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != code:
            raise RuntimeError(
                f"Structural CLI {args[0]} failed: {completed.stdout} {completed.stderr}"
            )
        return json.loads(completed.stdout)

    def execute(directory: Path, expected_tests: int) -> dict:
        checked = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        if f"Ran {expected_tests} tests" not in checked.stderr:
            raise RuntimeError("Application test discovery omitted dependent modules")
        completed = subprocess.run(
            [sys.executable, "-m", "app", "records.json"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        report = json.loads(completed.stdout)
        assert report["records"] == 30 and report["total"] == 870
        return {
            "application": report,
            "tests": expected_tests,
            "unittest_output": checked.stderr,
            "workloads": 240 + (60 if expected_tests == 5 else 0),
        }

    draft = cli("structure-check", "--rules", "draft.structure-profile.json")
    assert draft["ok"] and not (root / structure.PROFILE).exists()
    put(structure.PROFILE.as_posix(), json_bytes(profile))
    baseline = execute(root, 2)
    cli("baseline")
    assert cli("audit")["ok"]

    async def call(client: Client, name: str, args: dict) -> dict:
        result = await client.call_tool(name, args)
        block = result.content[0]
        if result.is_error or block.type != "text":
            raise RuntimeError(f"Structural MCP operation failed: {name}")
        return json.loads(block.text)

    async def agent_cycle() -> dict:
        async with Client(
            StdioTransport(
                command=sys.executable,
                args=[
                    "-m",
                    "bamboozle",
                    "--root",
                    str(root),
                    "serve-agent",
                    "--dict",
                    "mapping.dictionary.json",
                    "--policy",
                    "source.agent-policy.json",
                ],
                cwd=str(Path.cwd()),
            )
        ) as client:
            read = await call(client, "read_safe_file", {"file_path": "app/core.py"})
            assert "def omega(" in read["content"] and "alpha" not in json.dumps(read)
            for path, content in (
                ("app/core.py", "def omega(:\n"),
                ("app/missing.py", "def omega(x): return x\n"),
            ):
                denied = await client.call_tool(
                    "write_safe_file",
                    {"file_path": path, "content": content},
                    raise_on_error=False,
                )
                assert denied.is_error
            assert (root / "app/core.py").read_bytes() == MODULES[
                "app/core.py"
            ].encode()
            assert not (root / "app/missing.py").exists()
            saved = await call(
                client,
                "write_safe_file",
                {
                    "file_path": "app/core.py",
                    "content": read["content"].replace(
                        "MAX_AMOUNT = 1000000", "MAX_AMOUNT = 2000000"
                    ),
                    "expected_sha256": read["source_sha256"],
                    "expected_context_sha256": read["context_sha256"],
                },
            )
            private = await client.call_tool(
                "read_safe_file",
                {"file_path": ".vigiles/structure.json"},
                raise_on_error=False,
            )
            assert (
                private.is_error
                and saved["structure"]["profile_sha256"] == draft["profile_sha256"]
            )
            return {
                "saved": saved,
                "invalid_syntax_and_missing_companion_refused": True,
                "original_unchanged_on_refusal": True,
                "private_profile_refused": True,
            }

    agent = asyncio.run(asyncio.wait_for(agent_cycle(), timeout=45))
    updated = execute(root, 2)
    assert b"MAX_AMOUNT = 2000000" in (root / "app/core.py").read_bytes()
    exported = cli(
        "view-export",
        "source.agent-policy.json",
        "--output",
        ".local/views/development",
        "--dict",
        "mapping.dictionary.json",
    )
    view = root / exported["view"]
    assert execute(view, 2)["application"] == updated["application"]
    put("app/summary.py", SUMMARY.encode(), view)
    refused_review = cli("view-review", exported["view_id"], code=3)
    assert refused_review["structural_validation"]["findings"][0]["code"] == "companion"
    put("tests/test_summary.py", SUMMARY_TEST.encode(), view)
    put(
        "app/__main__.py",
        (view / "app/__main__.py")
        .read_bytes()
        .replace(b"from .core import omega", b"from .summary import rank")
        .replace(b"omega(json.loads", b"rank(json.loads"),
        view,
    )
    neutral = execute(view, 5)
    assert len(neutral["application"]["ranking"]) == 3

    async def operator_review() -> dict:
        async with Client(
            StdioTransport(
                command=sys.executable,
                args=["-m", "bamboozle", "--root", str(root), "serve"],
                cwd=str(Path.cwd()),
            )
        ) as client:
            checked = await call(client, "check_repository_structure", {})
            assert checked["ok"]
            reviewed = await call(
                client, "review_project_view", {"view_id": exported["view_id"]}
            )
            assert reviewed["ok"] and reviewed["structural_validation"]["ok"]
            return reviewed

    review = asyncio.run(asyncio.wait_for(operator_review(), timeout=45))
    original_write = view_import.atomic_write

    def fail(path: Path, data: bytes, *, overwrite: bool = False) -> None:
        if path == root / "tests/test_summary.py":
            raise OSError("Injected companion publication interruption")
        original_write(path, data, overwrite=overwrite)

    view_import.atomic_write = fail
    try:
        try:
            view_import.apply_import(
                Workspace(root), exported["view_id"], review["review_sha256"]
            )
        except OperationError as exc:
            failure = exc.report
        else:
            raise RuntimeError(
                "Fault injection did not interrupt multi-file publication"
            )
    finally:
        view_import.atomic_write = original_write
    assert (
        failure["journal_saved"]
        and failure["output_saved"]
        and not failure["receipt_saved"]
    )
    put(".vigiles/qualification/partial-import.json", json_bytes(failure))
    assert cli("view-status", exported["view_id"])["status"] == "pending"
    # A changed profile must not silently authorize the rest of the retained import.
    put(structure.PROFILE.as_posix(), json_bytes({**profile, "name": "revised"}))
    stale_recovery = cli(
        "view-apply",
        exported["view_id"],
        "--review-sha256",
        review["review_sha256"],
        "--recover",
        code=1,
    )
    assert not stale_recovery["receipt_saved"]
    put(".vigiles/qualification/stale-recovery.json", json_bytes(stale_recovery))
    put(structure.PROFILE.as_posix(), json_bytes(profile))

    async def operator_recover() -> dict:
        async with Client(
            StdioTransport(
                command=sys.executable,
                args=["-m", "bamboozle", "--root", str(root), "serve"],
                cwd=str(Path.cwd()),
            )
        ) as client:
            result = await call(
                client,
                "apply_project_view",
                {
                    "view_id": exported["view_id"],
                    "review_sha256": review["review_sha256"],
                    "recover": True,
                },
            )
            assert result["receipt_saved"]
            checked = await call(client, "check_repository_structure", {})
            assert checked["ok"]
            return result

    recovered = asyncio.run(asyncio.wait_for(operator_recover(), timeout=45))
    restored = execute(root, 5)
    assert restored["application"] == neutral["application"]
    assert b"from .core import alpha" in (root / "app/summary.py").read_bytes()
    refusals = {
        path.relative_to(root).as_posix(): sha256(path.read_bytes())
        for path in (root / structure.HISTORY).glob("*.json")
    }
    assert len(refusals) >= 3
    result = {
        "status": "passed",
        "structure_module": str(Path(structure.__file__).resolve()),
        "workspace": str(root),
        "draft": draft,
        "baseline": baseline,
        "agent": agent,
        "updated": updated,
        "refused_review": refused_review,
        "neutral": neutral,
        "review": review,
        "partial_import": failure,
        "stale_recovery": stale_recovery,
        "recovered": recovered,
        "restored": restored,
        "refusals": refusals,
        "final_structure": cli("structure-check"),
    }
    put(".vigiles/qualification/result.json", json_bytes(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.workspace)))
