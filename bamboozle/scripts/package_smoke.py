"""Exercise an installed wheel from a directory outside the source package."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

import bamboozle
from bamboozle import (
    agent_files,
    cipher,
    fixture_rules,
    fixtures,
    formats,
    lexicon,
    migration,
    project_views,
    restoration,
    return_validation,
    scm_mcp,
    view_import,
)
from bamboozle.dictionary import Dictionary
from bamboozle.service import OperationError
from bamboozle.utils import atomic_write, json_bytes, sha256
from bamboozle.workspace import Workspace
from workshop import examples, plans, process, recovery
from workshop import operations as ops
from workshop.pipeline import run_script
from workshop.scrape import bamb5
from workshop.scrape.alias_pass import Taxonomy


def smoke_capture() -> list[dict]:
    """Exercise noisy external scripts using only the installed adapter."""
    scrape = Path("capture-fixture")
    scrape.mkdir()
    script = (
        "import sys, threading, time\nfrom pathlib import Path\n"
        "def emit(stream, byte, ending):\n"
        "    for _ in range(64):\n"
        "        stream.write(byte * 65536)\n"
        "    stream.write(ending)\n    stream.flush()\n"
        "Path('partial.bin').write_bytes(b'retained synthetic output')\n"
        "threads = [threading.Thread(target=emit, args=(sys.stdout.buffer, b'O', b'out-end')),\n"
        "           threading.Thread(target=emit, args=(sys.stderr.buffer, b'E', b'err-end'))]\n"
        "for thread in threads: thread.start()\n"
        "for thread in threads: thread.join()\n"
        "if sys.argv[-1] == 'timeout': time.sleep(60)\n"
        "raise SystemExit(9 if sys.argv[-1] == 'failure' else 0)\n"
    )
    (scrape / "promote.py").write_text(script, encoding="utf-8")
    results = []
    for mode, status, code in (
        ("success", "completed", 0),
        ("failure", "failed", 9),
        ("timeout", "timed_out", 124),
    ):
        report = run_script(scrape, Path(sys.executable), "promote.py", mode, timeout=5)
        if report["status"] != status or report["returncode"] != code:
            raise RuntimeError(f"Installed capture outcome mismatch: {report}")
        for name, byte, ending, limit in (
            ("stdout", "O", "out-end", 8000),
            ("stderr", "E", "err-end", 4000),
        ):
            stream = report["capture"][name]
            if (
                report[name] != byte * (limit - len(ending)) + ending
                or not report[f"{name}_truncated"]
                or stream["bytes_read"] != 64 * 65536 + len(ending)
                or stream["bytes_retained"] != limit
            ):
                raise RuntimeError(f"Installed {name} capture mismatch: {stream}")
        if not report["capture"]["complete"] or not report["child_exited"]:
            raise RuntimeError(
                "Installed capture did not finish draining/reap the child"
            )
        if (scrape / "partial.bin").read_bytes() != b"retained synthetic output":
            raise RuntimeError("Installed adapter changed partial publication evidence")
        results.append(
            {
                "mode": mode,
                "status": status,
                "returncode": code,
                "capture": report["capture"],
            }
        )
    return results


def smoke_profanity() -> dict:
    """Exercise shipped vocabulary through CLI and both installed stdio surfaces."""
    root = Path("profanity-fixture").resolve()
    root.mkdir()
    original = (
        "\ufeff# f*ck; for fuck’s sake\r\n"
        "def fUcK(value):\r\n    return value + 1\r\n"
        "assert fUcK(3) == 4\r\n"
    ).encode("utf-8")
    expected = original.replace(b"value + 1", b"value * 2").replace(b"== 4", b"== 6")
    (root / "node.py").write_bytes(original)

    def cli(*arguments: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "bamboozle", "--root", str(root), *arguments],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(result.stdout)

    scanned = cli("scan", "node.py", "--profile", "profanity", "--max-matches", "2")
    if scanned["matched_terms"] != 4 or scanned["matches_complete"]:
        raise RuntimeError(
            "Installed profanity scan did not count/bound real vocabulary"
        )
    if scanned["counts"] != {"f*ck": 1, "for fuck’s sake": 1, "fuck": 2}:
        raise RuntimeError("Installed CLI JSON corrupted Unicode scan terms in transit")
    cli("encode", "node.py", "--profile", "profanity")
    cli("decode", "node_safe.py", "--strict")
    if (root / "node_safe_ready.py").read_bytes() != original:
        raise RuntimeError("Installed profile and case-marker round trip failed")
    stream = "F**K; for fuck’s sake; fUcK"
    command = Path(sys.executable).with_name(
        "scm-cipher.exe" if sys.platform == "win32" else "scm-cipher"
    )
    encoded = subprocess.run(
        [
            str(command),
            "--profile",
            "profanity",
            "--mode",
            "forward",
            "--input",
            "-",
            "--output",
            "-",
        ],
        input=stream.encode("utf-8"),
        capture_output=True,
        check=True,
        timeout=30,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    ).stdout.decode("utf-8")
    if (
        cipher.CipherEngine(Dictionary.load(profile="profanity")).decode(encoded)
        != stream
    ):
        raise RuntimeError("Installed cipher profile flags lost text")

    async def exercise() -> dict:
        command = Path(sys.executable).with_name(
            "scm-mcp.exe" if sys.platform == "win32" else "scm-mcp"
        )
        transport = StdioTransport(
            command=str(command),
            args=["--root", str(root), "--profile", "profanity"],
            cwd=str(root),
        )
        async with Client(transport) as client:
            if {tool.name for tool in await client.list_tools()} != {
                "read_safe_file",
                "write_safe_file",
            }:
                raise RuntimeError("Agent profile exposed operator tools")

            async def call(name: str, arguments: dict) -> dict:
                result = await client.call_tool(name, arguments)
                content = result.content[0]
                if result.is_error or content.type != "text":
                    raise RuntimeError("Installed profanity agent call failed")
                return json.loads(content.text)

            read = await call("read_safe_file", {"file_path": "node.py"})
            if (
                "fuck" in read["content"].casefold()
                or "__SCM_CASE_" not in read["content"]
            ):
                raise RuntimeError(
                    "Installed agent did not filter the mixed-case vocabulary"
                )
            edited = (
                read["content"]
                .replace("value + 1", "value * 2")
                .replace("== 4", "== 6")
            )
            saved = await call(
                "write_safe_file", {"file_path": "node.py", "content": edited}
            )
            identifier = cipher.CipherEngine(
                Dictionary.load(profile="profanity")
            ).encode("fUcK")
            generated = await call(
                "write_safe_file",
                {
                    "file_path": "generated.py",
                    "content": f"from node import {identifier}\nassert {identifier}(5) == 10\n",
                },
            )
            if (root / "node.py").read_bytes() != expected:
                raise RuntimeError(
                    "Installed agent edit lost case or original byte format"
                )
            if (root / saved["history"] / "original.bin").read_bytes() != original:
                raise RuntimeError(
                    "Installed profanity workflow did not preserve its original"
                )
            await asyncio.to_thread(
                subprocess.run,
                [sys.executable, str(root / "generated.py")],
                check=True,
                timeout=10,
            )
        operator = StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "bamboozle",
                "--root",
                str(root),
                "serve",
                "--profile",
                "profanity",
            ],
            cwd=str(root),
        )
        async with Client(operator) as client:
            result = await client.call_tool(
                "scan_file", {"source": "node.py", "max_matches": 2}
            )
            content = result.content[0]
            if content.type != "text" or json.loads(content.text)["matched_terms"] != 4:
                raise RuntimeError(
                    "Installed operator scan did not use the selected profile"
                )
        return {
            "edited": saved,
            "generated": generated,
            "dependent_program_executed": True,
        }

    return {
        "profile": "profanity",
        "profile_entries": 310,
        "default_entries": 415,
        "mapping_version": Dictionary.load(profile="profanity").version,
        "scan": scanned,
        "cli_exact_roundtrip": True,
        "cipher_stream_exact_roundtrip": True,
        "agent": asyncio.run(asyncio.wait_for(exercise(), timeout=45)),
    }


def smoke_project_views() -> dict:
    """Run complete installed project export/edit/return cycles and retained recovery."""
    original = b"\xef\xbb\xbfdef fUcK(value):\r\n    return value + 1\r\nassert fUcK(3) == 4\r\n"
    expected = original.replace(b"value + 1", b"value * 2").replace(b"== 4", b"== 6")
    production = b'{"prompt":"private authoritative content"}\n'
    token = cipher.CipherEngine(Dictionary.load()).encode("fUcK")

    def setup(mode: str) -> Path:
        root = Path(f"view-{mode}-fixture").resolve()
        (root / "src").mkdir(parents=True)
        (root / "src/node.py").write_bytes(original)
        (root / "src/obsolete.py").write_bytes(b"OLD = True\n")
        (root / "settings.json").write_bytes(b'{"enabled":false}\n')
        (root / "production.json").write_bytes(production)
        (root / "fixture.json").write_bytes(b'{"prompt":"neutral fixture"}\n')
        (root / "private.txt").write_bytes(b"excluded original")
        (root / ".env").write_bytes(b"UNSELECTED=private")
        (root / "view-policy.json").write_bytes(
            json_bytes(
                {
                    "schema_version": 1,
                    "files": {
                        "src/node.py": {"policy": "translate"},
                        "src/obsolete.py": {"policy": "copy"},
                        "settings.json": {"policy": "copy"},
                        "production.json": {
                            "policy": "fixture",
                            "fixture": "fixture.json",
                        },
                        "private.txt": {"policy": "exclude"},
                    },
                    "new_files": {"src": "translate"},
                }
            )
        )
        return root

    def cli(root: Path, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "bamboozle", "--root", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(result.stdout)

    def edit(root: Path, exported: dict) -> None:
        view = root / exported["view"]
        actual = {
            p.relative_to(view).as_posix() for p in view.rglob("*") if p.is_file()
        }
        if actual != {
            "src/node.py",
            "src/obsolete.py",
            "settings.json",
            "production.json",
            project_views.CONTRACT,
        }:
            raise RuntimeError(
                "Installed project view includes unselected or private files"
            )
        if b"fuck" in (view / "src/node.py").read_bytes().lower():
            raise RuntimeError("Installed view did not encode source vocabulary")
        subprocess.run(
            [sys.executable, str(view / "src/node.py")], check=True, timeout=10
        )
        node = view / "src/node.py"
        node.write_bytes(
            node.read_bytes()
            .replace(b"value + 1", b"value * 2")
            .replace(b"== 4", b"== 6")
        )
        (view / "src/generated.py").write_bytes(
            f"from node import {token}\nassert {token}(5) == 10\n".encode()
        )
        (view / "settings.json").write_bytes(b'{"enabled":true}\n')
        (view / "production.json").write_bytes(b'{"prompt":"agent edited fixture"}\n')
        (view / "src/obsolete.py").unlink()
        subprocess.run(
            [sys.executable, str(view / "src/generated.py")], check=True, timeout=10
        )

    def checked_review(root: Path, review: dict) -> None:
        if (
            not review["ok"]
            or review["action_count"] != 4
            or not review["review_sha256"]
        ):
            raise RuntimeError("Installed multi-file review failed")
        if {row["action"] for row in review["changes"]} != {
            "modify",
            "create",
            "delete",
            "fixture_ignored",
        }:
            raise RuntimeError("Installed review omitted a policy/action result")
        if (root / "src/node.py").read_bytes() != original or (
            root / "src/generated.py"
        ).exists():
            raise RuntimeError("Review changed authoritative source")

    def checked_result(
        root: Path, exported: dict, reviewed: dict, saved: dict, status: dict
    ) -> dict:
        if not saved["receipt_saved"] or status["status"] != "complete":
            raise RuntimeError("Installed import has no validated completion")
        if (root / "src/node.py").read_bytes() != expected:
            raise RuntimeError("Installed project return changed case/BOM/CRLF")
        if (root / "production.json").read_bytes() != production or (
            root / "src/obsolete.py"
        ).exists():
            raise RuntimeError("Installed fixture/deletion import policy failed")
        if (root / "settings.json").read_bytes() != b'{"enabled":true}\n':
            raise RuntimeError("Installed copy policy did not apply exact bytes")
        subprocess.run(
            [sys.executable, str(root / "src/generated.py")], check=True, timeout=10
        )
        directory, manifest, _, _, _ = project_views.load_view(
            Workspace(root), exported["view_id"]
        )
        if (
            project_views.blob(
                Workspace(root),
                directory,
                manifest["files"]["src/node.py"]["original_sha256"],
            )
            != original
        ):
            raise RuntimeError("Installed import lost its private original snapshot")
        return {
            "root": str(root),
            "export": exported,
            "review_sha256": reviewed["review_sha256"],
            "action_count": reviewed["action_count"],
            "applied": saved,
            "status": status,
            "encoded_and_restored_dependent_programs_executed": True,
            "private_original_preserved": True,
            "fixture_source_preserved": True,
        }

    root = setup("cli")
    exported = cli(
        root, "view-export", "view-policy.json", "--output", ".local/views/task"
    )
    edit(root, exported)
    reviewed = cli(
        root, "view-review", exported["view_id"], "--delete", "src/obsolete.py"
    )
    checked_review(root, reviewed)
    applied = cli(
        root,
        "view-apply",
        exported["view_id"],
        "--review-sha256",
        reviewed["review_sha256"],
        "--delete",
        "src/obsolete.py",
    )
    cli_result = checked_result(
        root, exported, reviewed, applied, cli(root, "view-status", exported["view_id"])
    )

    async def mcp_cycle() -> dict:
        root = setup("mcp")
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "bamboozle", "--root", str(root), "serve"],
            cwd=str(root),
        )
        async with Client(transport) as client:

            async def call(name: str, arguments: dict) -> dict:
                result = await client.call_tool(name, arguments)
                content = result.content[0]
                if result.is_error or content.type != "text":
                    raise RuntimeError(f"Installed project view MCP failed: {name}")
                return json.loads(content.text)

            exported = await call(
                "export_project_view",
                {"config": "view-policy.json", "output": ".local/views/task"},
            )
            edit(root, exported)
            args = {"view_id": exported["view_id"], "delete_files": ["src/obsolete.py"]}
            reviewed = await call("review_project_view", args)
            checked_review(root, reviewed)
            applied = await call(
                "apply_project_view",
                {**args, "review_sha256": reviewed["review_sha256"]},
            )
            status = await call("project_view_status", {"view_id": exported["view_id"]})
            return checked_result(root, exported, reviewed, applied, status)

    mcp_result = asyncio.run(asyncio.wait_for(mcp_cycle(), timeout=60))
    root = setup("recovery")
    workspace = Workspace(root)
    exported = cli(
        root, "view-export", "view-policy.json", "--output", ".local/views/task"
    )
    edit(root, exported)
    reviewed = cli(
        root, "view-review", exported["view_id"], "--delete", "src/obsolete.py"
    )
    checked_review(root, reviewed)

    def interrupt(path: Path, data: bytes, *, overwrite: bool = False) -> None:
        if path == root / "src/generated.py":
            raise OSError("Synthetic installed import interruption")
        atomic_write(path, data, overwrite=overwrite)

    with patch.object(view_import, "atomic_write", interrupt):
        try:
            view_import.apply_import(
                workspace,
                exported["view_id"],
                reviewed["review_sha256"],
                delete_files=["src/obsolete.py"],
            )
        except OperationError as exc:
            partial = exc.report
        else:
            raise RuntimeError("Installed fault injection did not interrupt import")
    pending = cli(root, "view-status", exported["view_id"])
    if (
        pending["status"] != "pending"
        or partial["receipt_saved"]
        or not partial["output_saved"]
    ):
        raise RuntimeError("Installed partial import reported false completion")
    if (
        pending["files"]["settings.json"] != "applied"
        or pending["files"]["src/node.py"] != "pending"
    ):
        raise RuntimeError("Installed partial import inspection lost per-file state")
    applied = cli(
        root,
        "view-apply",
        exported["view_id"],
        "--review-sha256",
        reviewed["review_sha256"],
        "--recover",
    )
    recovered = checked_result(
        root, exported, reviewed, applied, cli(root, "view-status", exported["view_id"])
    )
    return {
        "cli": cli_result,
        "mcp": mcp_result,
        "recovery": {**recovered, "partial": partial, "pending": pending},
    }


def smoke_fixtures() -> dict:
    """Installed CLI/stdio preparation, real catalog development and restored execution."""
    scenario = runpy.run_path(str(Path(__file__).with_name("fixture_scenario.py")))
    root = Path("generated-fixture-workflow").resolve()
    root.mkdir()

    def setup(mode: str) -> Path:
        selected = root / mode
        (selected / "src").mkdir(parents=True)
        (selected / "src/renderer.py").write_text(
            "def fuck(record, values):\n    return record['prompt'].format_map(values)\n",
            encoding="utf-8",
        )
        (selected / "templates.json").write_bytes(
            b"\xef\xbb\xbf"
            + json_bytes(scenario["source_document"]()).replace(b"\n", b"\r\n")
        )
        (selected / "templates.fixture-rules.json").write_bytes(
            json_bytes(scenario["rules_document"]())
        )
        Dictionary({"fuck": "assemble"}).save(selected / "mapping.dictionary.json")
        policy = {
            "schema_version": 3,
            "files": {
                "templates.json": {
                    "policy": "fixture",
                    "rules": "templates.fixture-rules.json",
                },
                "src/renderer.py": {"policy": "translate"},
            },
            "new_files": {"src": "translate"},
        }
        (selected / "policy.json").write_bytes(json_bytes(policy))
        return selected

    def execute(selected: Path, view: Path) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "src/run_catalog.py",
                str(view / fixtures.TEMPLATE_CASES),
            ],
            cwd=selected,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        if result.stdout.strip() != "catalog-pipeline-ok":
            raise RuntimeError(
                "Installed catalog did not finish valid/error/binding checks"
            )

    def confirm(selected: Path, original: bytes) -> None:
        view = selected / "neutral"
        if (selected / "templates.json").read_bytes() != original:
            raise RuntimeError("Generated fixture replaced production content")
        if "from renderer import fuck" not in (selected / "src/catalog.py").read_text():
            raise RuntimeError("Generated dependent catalog code was not decoded")
        for name in (*fixtures.GENERATED_FILES, "templates.json"):
            raw = (view / name).read_bytes()
            if b"PRIVATE_SOURCE" in raw or b"PRIVATE_NOTE" in raw:
                raise RuntimeError("Prepared fixture controls leaked replaced prose")
        if any((selected / name).exists() for name in fixtures.GENERATED_FILES):
            raise RuntimeError("Generated fixture controls entered source import")
        if "`assemble`" not in (view / fixtures.TEMPLATE_CONTRACT).read_text():
            raise RuntimeError("Contract did not bind the encoded consumer call path")
        execute(selected, view)

    selected = setup("cli")
    original = (selected / "templates.json").read_bytes()

    def cli(*args: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "bamboozle", "--root", str(selected), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(result.stdout)

    preview = cli(
        "fixture-preview", "templates.json", "--rules", "templates.fixture-rules.json"
    )
    if not preview["ok"] or len(preview["cases"]) != 8:
        raise RuntimeError("Installed fixture preparation lost checked cases")
    exported = cli(
        "view-export",
        "policy.json",
        "--output",
        "neutral",
        "--dict",
        "mapping.dictionary.json",
    )
    validation = cli(
        "fixture-validate",
        "neutral/templates.json",
        "--contract",
        "neutral/" + fixtures.TEMPLATE_SCHEMA,
        "--fixture",
        "templates.json",
    )
    if not validation["ok"]:
        raise RuntimeError("Installed public fixture validation failed")
    scenario["write_agent_return"](selected / "neutral")
    execute(selected / "neutral", selected / "neutral")
    review = cli("view-review", exported["view_id"])
    applied = cli(
        "view-apply", exported["view_id"], "--review-sha256", review["review_sha256"]
    )
    confirm(selected, original)
    cli_result = {
        "root": str(selected),
        "preview": preview,
        "export": exported,
        "review": review,
        "apply": applied,
        "original_sha256": sha256(original),
        "status": cli("view-status", exported["view_id"]),
        "neutral_and_original_catalog_executed": True,
    }

    async def stdio() -> dict:
        selected = setup("mcp")
        original = (selected / "templates.json").read_bytes()
        transport = StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "bamboozle",
                "--root",
                str(selected),
                "serve",
                "--dict",
                "mapping.dictionary.json",
            ],
        )
        async with Client(transport) as client:

            async def call(name: str, args: dict) -> dict:
                result = await client.call_tool(name, args)
                content = result.content[0]
                if content.type != "text":
                    raise RuntimeError("Unexpected fixture MCP response")
                report = json.loads(content.text)
                if report.get("ok") is False:
                    raise RuntimeError(f"Fixture MCP operation failed: {report}")
                return report

            preview = await call(
                "preview_template_fixture",
                {"source": "templates.json", "rules": "templates.fixture-rules.json"},
            )
            exported = await call(
                "export_project_view", {"config": "policy.json", "output": "neutral"}
            )
            await call(
                "validate_template_fixture",
                {
                    "source": "neutral/templates.json",
                    "contract": "neutral/" + fixtures.TEMPLATE_SCHEMA,
                    "fixture": "templates.json",
                },
            )
            scenario["write_agent_return"](selected / "neutral")
            execute(selected / "neutral", selected / "neutral")
            review = await call("review_project_view", {"view_id": exported["view_id"]})
            applied = await call(
                "apply_project_view",
                {
                    "view_id": exported["view_id"],
                    "review_sha256": review["review_sha256"],
                },
            )
            state = await call("project_view_status", {"view_id": exported["view_id"]})
        confirm(selected, original)
        agent = StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "bamboozle",
                "--root",
                str(selected),
                "serve-agent",
                "--dict",
                "mapping.dictionary.json",
            ],
        )
        async with Client(agent) as client:
            if {tool.name for tool in await client.list_tools()} != {
                "read_safe_file",
                "write_safe_file",
            }:
                raise RuntimeError("Fixture operator tools leaked into agent surface")
            denied = await client.call_tool(
                "read_safe_file",
                {"file_path": "templates.fixture-rules.json"},
                raise_on_error=False,
            )
            if not denied.is_error:
                raise RuntimeError(
                    "Private fixture rules were exposed to source-agent tools"
                )
        return {
            "root": str(selected),
            "preview": preview,
            "export": exported,
            "review": review,
            "apply": applied,
            "status": state,
            "original_sha256": sha256(original),
            "neutral_and_original_catalog_executed": True,
            "source_agent_surface_and_private_rules_verified": True,
        }

    return {
        "cli": cli_result,
        "mcp": asyncio.run(asyncio.wait_for(stdio(), timeout=90)),
    }


def smoke_lexicon() -> dict:
    """Author real variants, then execute a returned two-module numerical pipeline."""
    root = Path("lexicon-fixture").resolve()
    root.mkdir()
    baseline = Dictionary(
        {
            "fuck": "pulse",
            "fucked": "pulsed",
            "shit": "residue",
            "obsolete": "retired_signal",
        },
        "stats-1",
    )
    baseline.save(root / "base.json")
    (root / "base.json").write_bytes(
        b"\xef\xbb\xbf" + (root / "base.json").read_bytes().replace(b"\n", b"\r\n")
    )

    def cli(*args: str) -> dict:
        run = subprocess.run(
            [sys.executable, "-m", "bamboozle", "--root", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(run.stdout)

    original_json = b'{ "prompt": "f\\u0075ck", "id": "fuck" }\r\n'
    (root / "legacy.json").write_bytes(original_json)
    cli(
        "encode",
        "legacy.json",
        "--dict",
        "base.json",
        "--format",
        "json",
        "--json-pointer",
        "/prompt",
    )
    cli(
        "lexicon-export",
        "--dict",
        "base.json",
        "--mapping-version",
        "stats-2",
        "-o",
        "base.lexicon.json",
    )
    entries = cli("lexicon-query", "base.lexicon.json")["entries"]
    identifiers = {entry["bam"]: entry["id"] for entry in entries}
    changes = {
        "schema_version": 1,
        "mapping_version": "stats-2",
        "remove": [identifiers["fucked"], identifiers["obsolete"]],
        "upsert": [
            {
                "id": identifiers["fuck"],
                "kind": "verb",
                "forms": [
                    {"bam": "fuck", "boozle": "impulse", "label": "base"},
                    {"bam": "fucked", "boozle": "impulsed", "label": "past"},
                    {"bam": "fucking", "boozle": "impulsing", "label": "progressive"},
                    {"bam": "f*ck", "boozle": "masked_impulse", "label": "spelling"},
                ],
            },
            {
                "id": "batch",
                "forms": [{"bam": "clusterfuck", "boozle": "cascade_failure"}],
            },
        ],
    }
    (root / "update.lexicon-patch.json").write_bytes(json_bytes(changes))
    arguments = (
        "lexicon-edit",
        "base.lexicon.json",
        "--patch",
        "update.lexicon-patch.json",
        "-o",
        "stats.lexicon.json",
    )
    edit = cli(*arguments)
    cli(*arguments, "--apply", "--review-sha256", edit["review_sha256"])
    (root / "stats.lexicon.json").write_bytes(
        b"\xef\xbb\xbf"
        + (root / "stats.lexicon.json").read_bytes().replace(b"\n", b"\r\n")
    )
    arguments = (
        "lexicon-compile",
        "stats.lexicon.json",
        "--base",
        "base.json",
        "-o",
        "stats.dictionary.json",
    )
    review = cli(*arguments)
    if review["change_counts"] != {"added": 3, "changed": 2, "removed": 1}:
        raise RuntimeError("Installed lexicon comparison lost exact changes")
    cli(*arguments, "--apply", "--review-sha256", review["review_sha256"])
    verified = cli("lexicon-verify", "stats.dictionary.json")
    cli("decode", "legacy_safe.json", "--strict")
    if (root / "legacy_safe_ready.json").read_bytes() != original_json:
        raise RuntimeError("New lexicon reinterpreted an existing structured ledger")

    (root / "src").mkdir()
    original_program = "from dataclasses import dataclass\n\n@dataclass\nclass Shit:\n    count: int = 0\n    mean: float = 0.0\n\n    def fuck(self, value):\n        self.count += 1\n        self.mean += (value - self.mean) / self.count\n"
    (root / "src/model.py").write_text(original_program, encoding="utf-8")
    (root / "guide.md").write_text(
        "# Fuck\n\n`fuck` stays literal.\n", encoding="utf-8"
    )
    policy = {
        "schema_version": 2,
        "files": {
            "src/model.py": {"policy": "translate"},
            "guide.md": {"policy": "translate", "document": {"format": "markdown"}},
        },
        "new_files": {"src": "translate"},
    }
    (root / "policy.json").write_bytes(json_bytes(policy))
    exported = cli(
        "view-export",
        "policy.json",
        "--output",
        "neutral",
        "--dict",
        "stats.dictionary.json",
    )
    neutral = root / "neutral"
    if "class Residue" not in (neutral / "src/model.py").read_text():
        raise RuntimeError("Compiled lexicon was not used by project export")
    # Representative returned implementation: stable one-pass variance, finite-input
    # validation, an independently generated batch module and error-path execution.
    (neutral / "src/model.py").write_text(
        "from dataclasses import dataclass\nfrom math import isfinite\n\n@dataclass\nclass Residue:\n"
        "    count: int = 0\n    mean: float = 0.0\n    m2: float = 0.0\n\n"
        "    def impulse(self, value):\n        if not isfinite(value):\n            raise ValueError('finite samples required')\n"
        "        self.count += 1\n        delta = value - self.mean\n        self.mean += delta / self.count\n"
        "        self.m2 += delta * (value - self.mean)\n\n"
        "    def impulsed(self):\n        if self.count < 2:\n            raise ValueError('two samples required')\n"
        "        return self.m2 / (self.count - 1)\n",
        encoding="utf-8",
    )
    (neutral / "src/batch.py").write_text(
        "from math import isclose\nfrom statistics import variance\nfrom model import Residue\n\n"
        "def cascade_failure(values):\n    state = Residue()\n    for value in values:\n        state.impulse(value)\n"
        "    return state.count, state.mean, state.impulsed()\n\n"
        "values = [100000001, 100000002, 100000007, 100000013]\n"
        "count, mean, sample_variance = cascade_failure(values)\nassert count == 4\nassert mean == sum(values) / 4\n"
        "assert isclose(sample_variance, variance(values), rel_tol=1e-8)\n"
        "for invalid in ([], [1.0], [float('nan'), 1.0], [float('inf'), 2.0]):\n"
        "    try:\n        cascade_failure(invalid)\n    except ValueError:\n        pass\n"
        "    else:\n        raise AssertionError('invalid batch accepted')\nprint('stats-pipeline-ok')\n",
        encoding="utf-8",
    )
    (neutral / "guide.md").write_text(
        "# Impulse\n\n`fuck` stays literal.\n\nImpulsing supports variance.\n",
        encoding="utf-8",
    )

    def execute(directory: Path) -> None:
        result = subprocess.run(
            [sys.executable, "src/batch.py"],
            cwd=directory,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if result.stdout.strip() != "stats-pipeline-ok":
            raise RuntimeError(
                "Compiled dictionary program did not finish its assertions"
            )

    execute(neutral)
    returned = cli("view-review", exported["view_id"])
    applied = cli(
        "view-apply", exported["view_id"], "--review-sha256", returned["review_sha256"]
    )
    execute(root)
    if (
        "class Shit" not in (root / "src/model.py").read_text()
        or "def clusterfuck" not in (root / "src/batch.py").read_text()
        or "Fucking supports variance." not in (root / "guide.md").read_text()
    ):
        raise RuntimeError(
            "Returned generated program/prose did not recover source vocabulary"
        )

    async def mcp_cycle() -> dict:
        transport = StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "bamboozle",
                "--root",
                str(root),
                "serve",
                "--dict",
                "stats.dictionary.json",
            ],
        )
        async with Client(transport) as client:

            async def call(name: str, args: dict) -> dict:
                result = await client.call_tool(name, args)
                content = result.content[0]
                if content.type != "text":
                    raise RuntimeError("Unexpected lexicon MCP response")
                return json.loads(content.text)

            await call(
                "export_dictionary_lexicon",
                {"output": "mcp.lexicon.json", "mapping_version": "stats-3"},
            )
            await call("check_dictionary_lexicon", {"source": "mcp.lexicon.json"})
            entry = (
                await call(
                    "query_dictionary_lexicon",
                    {"source": "mcp.lexicon.json", "query": "fuck", "side": "bam"},
                )
            )["entries"]
            source_id = next(row["id"] for row in entry if row["bam"] == "fuck")
            (root / "mcp.lexicon-patch.json").write_bytes(
                json_bytes(
                    {
                        "schema_version": 1,
                        "mapping_version": "stats-3",
                        "remove": [],
                        "upsert": [
                            {
                                "id": source_id,
                                "forms": [{"bam": "fuck", "boozle": "actuate"}],
                            }
                        ],
                    }
                )
            )
            edited = await call(
                "preview_lexicon_edit",
                {
                    "source": "mcp.lexicon.json",
                    "patch": "mcp.lexicon-patch.json",
                    "output": "mcp-edited.lexicon.json",
                },
            )
            await call("apply_preview", {"plan_id": edited["plan_id"]})
            selected = {
                "source": "mcp-edited.lexicon.json",
                "output": "mcp.dictionary.json",
                "base": "stats.dictionary.json",
            }
            stale = await call("preview_lexicon_compilation", selected)
            candidate = root / "mcp-edited.lexicon.json"
            candidate.write_bytes(candidate.read_bytes() + b" ")
            refused = await client.call_tool(
                "apply_preview", {"plan_id": stale["plan_id"]}, raise_on_error=False
            )
            if not refused.is_error or (root / "mcp.dictionary.json").exists():
                raise RuntimeError("Installed MCP accepted a stale compiler review")
            fresh = await call("preview_lexicon_compilation", selected)
            await call("apply_preview", {"plan_id": fresh["plan_id"]})
            receipt = await call(
                "verify_dictionary_compilation", {"source": "mcp.dictionary.json"}
            )
            encoded = await call(
                "preview_file",
                {
                    "source": "legacy.json",
                    "output": "mcp-safe.json",
                    "dictionary": "mcp.dictionary.json",
                    "document_format": "json",
                    "json_pointers": ["/prompt"],
                },
            )
            await call("apply_preview", {"plan_id": encoded["plan_id"]})
            (root / "mcp-return.json").write_text(
                '{ "prompt": "actuate updated", "id": "fuck" }\n', encoding="utf-8"
            )
            decoded = await call(
                "preview_file",
                {
                    "source": "mcp-return.json",
                    "output": "mcp-restored.json",
                    "reverse": True,
                    "ledger": ".mcp-safe.json.scm_ledger.json",
                },
            )
            await call("apply_preview", {"plan_id": decoded["plan_id"]})
            if json.loads((root / "mcp-restored.json").read_bytes()) != {
                "prompt": "fuck updated",
                "id": "fuck",
            }:
                raise RuntimeError(
                    "Compiled MCP lexicon failed a structured edited return"
                )
        agent_transport = StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "bamboozle",
                "--root",
                str(root),
                "serve-agent",
                "--dict",
                "mcp.dictionary.json",
            ],
        )
        async with Client(agent_transport) as client:
            tools = sorted(tool.name for tool in await client.list_tools())
            if tools != ["read_safe_file", "write_safe_file"]:
                raise RuntimeError("Compiler leaked into source-agent tool pool")
            rejected = await client.call_tool(
                "read_safe_file",
                {"file_path": "mcp.lexicon.json"},
                raise_on_error=False,
            )
            if not rejected.is_error:
                raise RuntimeError("Source-agent tool exposed lexicon metadata")
        return {
            "verification": receipt,
            "stale_review_refused": True,
            "structured_edited_return": True,
            "source_agent_tools": tools,
            "private_lexicon_read_refused": True,
        }

    return {
        "root": str(root),
        "review": review,
        "verification": verified,
        "view_export": exported,
        "view_import": applied,
        "neutral_and_restored_statistics_pipeline_executed": True,
        "historical_json_bytes_restored": True,
        "mcp": asyncio.run(asyncio.wait_for(mcp_cycle(), timeout=90)),
    }


def smoke_document_formats() -> dict:
    """Installed adapters, edited return, mixed views and separate authenticated crypto."""
    root = Path("document-fixture").resolve()
    root.mkdir()
    original = b'\xef\xbb\xbf{\r\n "prompt":"\\u0066uck", "identifier":"fuck", "value":1e+03, "module":"def fuck(x): return x+1"\r\n}\r\n'
    markdown = b"\xef\xbb\xbf# **fuck**\r\n\r\n`fuck` and https://example.com/fuck stay literal.\r\n\r\n```python\r\ndef fuck(x):\r\n    return x + 1\r\nassert fuck(3) == 4\r\n```\r\n"
    (root / "template.json").write_bytes(original)
    (root / "guide.md").write_bytes(markdown)

    def cli(*args: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "bamboozle", "--root", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(result.stdout)

    def execute_files(json_path: Path, markdown_path: Path) -> None:
        document = json.loads(json_path.read_bytes())
        if document["identifier"] != "fuck" or document["value"] != 1000:
            raise RuntimeError(
                "Structured conversion altered unselected identity or numeric fields"
            )
        subprocess.run(
            [sys.executable, "-c", document["module"] + "\nassert fuck(3) == 4"],
            check=True,
            timeout=10,
        )
        match = re.search(
            rb"```python\r\n(.*?)```", markdown_path.read_bytes(), re.DOTALL
        )
        if match is None:
            raise RuntimeError("Structured conversion removed a code fence")
        subprocess.run(
            [sys.executable, "-c", match.group(1).decode()], check=True, timeout=10
        )

    encoded_json = cli(
        "encode",
        "template.json",
        "--profile",
        "profanity",
        "--format",
        "json",
        "--json-pointer",
        "/prompt",
    )
    encoded_markdown = cli(
        "encode", "guide.md", "--profile", "profanity", "--format", "markdown"
    )
    for name in ("template_safe.json", "guide_safe.md"):
        cli("verify", name)
        cli("decode", name, "--strict")
    if (root / "template_safe_ready.json").read_bytes() != original or (
        root / "guide_safe_ready.md"
    ).read_bytes() != markdown:
        raise RuntimeError("Installed structured strict decode lost original bytes")
    if b"**kinetic_impulse**" not in (root / "guide_safe.md").read_bytes():
        raise RuntimeError("Installed Markdown emphasis was not translated")
    execute_files(root / "template_safe.json", root / "guide_safe.md")

    # Encryption is an explicit operator layer around the encoded payload. Its
    # key stays in this synthetic runtime fixture and is never included in reports.
    cli("keygen", "--key", "transport.key")
    sealed = cli(
        "seal",
        "template_safe.json",
        "--key",
        "transport.key",
        "--recipient",
        "fixture-operator",
        "-o",
        "packet.bam",
    )
    opened = cli(
        "open",
        "packet.bam",
        "--key",
        "transport.key",
        "--recipient",
        "fixture-operator",
        "-o",
        "returned.json",
    )
    cli(
        "decode",
        "returned.json",
        "--ledger",
        ".template_safe.json.scm_ledger.json",
        "--strict",
    )
    if (root / "returned_ready.json").read_bytes() != original:
        raise RuntimeError("Installed encode/encrypt/decrypt/decode composition failed")
    term_cipher = AESSIV(bytes(range(64)))
    token = bamb5.encode("fixture-domain-term", term_cipher)
    if (
        not token.startswith("b5_")
        or bamb5.decode(token, term_cipher) != "fixture-domain-term"
    ):
        raise RuntimeError(
            "Installed BAMb5 token cipher did not retain its separate round trip"
        )

    async def mcp_cycle() -> dict:
        transport = StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "bamboozle",
                "--root",
                str(root),
                "serve",
                "--profile",
                "profanity",
            ],
            cwd=str(root),
        )
        async with Client(transport) as client:

            async def call(name: str, arguments: dict) -> dict:
                result = await client.call_tool(name, arguments)
                if result.is_error or result.content[0].type != "text":
                    raise RuntimeError(f"Installed structured MCP call failed: {name}")
                return json.loads(result.content[0].text)

            previewed = await call(
                "preview_file",
                {
                    "source": "template.json",
                    "output": "mcp_safe.json",
                    "document_format": "json",
                    "json_pointers": ["/prompt"],
                },
            )
            await call("apply_preview", {"plan_id": previewed["plan_id"]})
            path = root / "mcp_safe.json"
            path.write_bytes(
                path.read_bytes().replace(
                    b'"kinetic_impulse"', b'"kinetic_impulse updated"'
                )
            )
            reverse = await call(
                "preview_file",
                {
                    "source": "mcp_safe.json",
                    "output": "mcp_return.json",
                    "reverse": True,
                },
            )
            saved = await call("apply_preview", {"plan_id": reverse["plan_id"]})
            if (
                json.loads((root / "mcp_return.json").read_bytes())["prompt"]
                != "fuck updated"
            ):
                raise RuntimeError(
                    "Installed structured MCP did not restore edited selected values"
                )
            policy = {
                "schema_version": 2,
                "files": {
                    "guide.md": {
                        "policy": "translate",
                        "document": {"format": "markdown"},
                    },
                    "template.json": {
                        "policy": "translate",
                        "document": {"format": "json", "pointers": ["/prompt"]},
                    },
                },
                "new_files": {
                    "configs": {
                        "policy": "translate",
                        "document": {"format": "json", "pointers": ["/prompt"]},
                    }
                },
            }
            (root / "policy.json").write_bytes(json_bytes(policy))
            exported = await call(
                "export_project_view", {"config": "policy.json", "output": "view"}
            )
            view = root / "view"
            guide = view / "guide.md"
            guide.write_bytes(
                guide.read_bytes() + b"\r\n**kinetic_impulse** added prose\r\n"
            )
            (view / "configs").mkdir()
            (view / "configs/new.json").write_bytes(
                b'{"prompt":"kinetic_impulse generated","identifier":"kinetic_impulse"}'
            )
            reviewed = await call(
                "review_project_view", {"view_id": exported["view_id"]}
            )
            if not reviewed["ok"] or reviewed["action_count"] != 2:
                raise RuntimeError("Installed structured project review failed")
            imported = await call(
                "apply_project_view",
                {
                    "view_id": exported["view_id"],
                    "review_sha256": reviewed["review_sha256"],
                },
            )
            if (
                not imported["receipt_saved"]
                or (root / "template.json").read_bytes() != original
            ):
                raise RuntimeError(
                    "Installed structured view lost exact original JSON context"
                )
            if json.loads((root / "configs/new.json").read_bytes()) != {
                "prompt": "fuck generated",
                "identifier": "kinetic_impulse",
            }:
                raise RuntimeError(
                    "Installed generated config ignored its selected fields"
                )
            if (
                not (root / "guide.md")
                .read_bytes()
                .endswith(b"**fuck** added prose\r\n")
            ):
                raise RuntimeError("Installed project did not decode formatted prose")
            execute_files(root / "template.json", root / "guide.md")
            return {
                "edited_return": saved,
                "export": exported,
                "review_sha256": reviewed["review_sha256"],
                "imported": imported,
                "neutral_and_returned_code_executed": True,
            }

    mcp = asyncio.run(asyncio.wait_for(mcp_cycle(), timeout=90))
    return {
        "root": str(root),
        "json_encoded": encoded_json,
        "markdown_encoded": encoded_markdown,
        "strict_original_bytes_restored": True,
        "protected_code_executed": True,
        "encryption_composition": {
            "sealed": sealed,
            "opened": opened,
            "decoded_original_bytes_restored": True,
        },
        "bamb5_separate_authenticated_roundtrip": True,
        "operator_mcp": mcp,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args()
    package_file = Path(bamboozle.__file__).resolve()
    if not package_file.is_relative_to(args.prefix.resolve()):
        raise RuntimeError(
            f"Smoke test loaded a source checkout instead of the wheel: {package_file}"
        )
    if not Path(process.__file__).resolve().is_relative_to(args.prefix.resolve()):
        raise RuntimeError("Capture module was not loaded from the installed wheel")
    if not Path(plans.__file__).resolve().is_relative_to(args.prefix.resolve()):
        raise RuntimeError("Prepared plans were not loaded from the installed wheel")
    if not Path(recovery.__file__).resolve().is_relative_to(args.prefix.resolve()):
        raise RuntimeError(
            "Recovery diagnostics were not loaded from the installed wheel"
        )
    for module in (
        restoration,
        return_validation,
        migration,
        cipher,
        agent_files,
        scm_mcp,
        project_views,
        view_import,
        formats,
        lexicon,
        fixture_rules,
        fixtures,
    ):
        if module.__file__ is None or not Path(
            module.__file__
        ).resolve().is_relative_to(args.prefix.resolve()):
            raise RuntimeError(
                "Translation workflow was not loaded from the installed wheel"
            )
    mapping = Dictionary.load()
    if (
        len(mapping) != 415
        or len(Dictionary.load(profile="profanity")) != 310
        or not Taxonomy.load().canon_cat
    ):
        raise RuntimeError("Packaged dictionary or taxonomy resource is missing")
    Path("mapping.json").write_text('{"alpha":"beta"}', encoding="utf-8")
    original = b"\xef\xbb\xbfAlpha beta\r\n"
    Path("source.txt").write_bytes(original)

    def run(*arguments: str, code: int = 0) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "bamboozle", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != code:
            raise RuntimeError(
                f"Installed command failed: {arguments}: {result.stdout} {result.stderr}"
            )
        return json.loads(result.stdout)

    run("validate")
    run("encode", "source.txt", "--dict", "mapping.json")
    run("verify", "source_safe.txt")
    run("decode", "source_safe.txt", "--strict")
    if Path("source_safe_ready.txt").read_bytes() != original:
        raise RuntimeError("Installed translation did not preserve original bytes")
    program = b"\xef\xbb\xbfdef alpha(x):\r\n    return x + 1\r\nassert alpha(3) == 4\r\n# beta is literal\r\n"
    Path("agent-original.py").write_bytes(program)
    run("encode", "agent-original.py", "--dict", "mapping.json")
    edited_path = Path("agent-original_safe.py")
    edited_path.write_bytes(
        edited_path.read_bytes().replace(b"x + 1", b"x * 2").replace(b"== 4", b"== 6")
    )
    unchanged_inputs = {
        p: p.read_bytes()
        for p in (
            Path("agent-original.py"),
            edited_path,
            Path(".agent-original_safe.py.scm_ledger.json"),
        )
    }
    restore_arguments = (
        "restore-edits",
        str(edited_path),
        "--original",
        "agent-original.py",
    )
    conflict = run(*restore_arguments, code=3)
    if conflict["status"] != "conflict" or conflict["conflict_count"] != 1:
        raise RuntimeError("Installed edited restoration did not expose ambiguity")
    review = run(*restore_arguments, "--terms", "translate", "-o", "agent-restored.py")
    saved = run(
        *restore_arguments,
        "--terms",
        "translate",
        "-o",
        "agent-restored.py",
        "--apply",
        "--review-sha256",
        review["review_sha256"],
    )
    restored_verification = run("verify-restoration", saved["output"])
    subprocess.run([sys.executable, saved["output"]], check=True, timeout=10)
    expected_program = program.replace(b"x + 1", b"x * 2").replace(b"== 4", b"== 6")
    if Path(saved["output"]).read_bytes() != expected_program or any(
        p.read_bytes() != raw for p, raw in unchanged_inputs.items()
    ):
        raise RuntimeError(
            "Installed restoration lost edits or changed its retained inputs"
        )
    strict_rejection = run(
        "decode", str(edited_path), "--strict", "-o", "strict.py", code=1
    )
    if "Encoded file changed" not in strict_rejection.get("error", ""):
        raise RuntimeError("Strict decode lost its unchanged-file guarantee")
    normal_edit = run("decode", str(edited_path), "-o", "agent-normal-edit.py")
    if Path(normal_edit["output"]).read_bytes() != expected_program:
        raise RuntimeError("Normal decode did not preserve the agent's edits")
    subprocess.run([sys.executable, normal_edit["output"]], check=True, timeout=10)
    # A new program has no original counterpart or per-file ledger.
    generated = b'def beta(x):\n    return x * 3\nassert beta(4) == 12\nassert "__SCM_LIT_beta__" == "bet" + "a"\n'
    generated_expected = b'def alpha(x):\n    return x * 3\nassert alpha(4) == 12\nassert "beta" == "bet" + "a"\n'
    Path("AGENT_fix.py").write_bytes(generated)
    normal_generated = run(
        "decode", "AGENT_fix.py", "--ledger", ".agent-original_safe.py.scm_ledger.json"
    )
    if Path(normal_generated["output"]).read_bytes() != generated_expected:
        raise RuntimeError("Normal decode did not restore newly generated source")
    subprocess.run([sys.executable, normal_generated["output"]], check=True, timeout=10)
    compatibility = subprocess.run(
        [
            sys.executable,
            "-m",
            "bamboozle.translate",
            "--mode",
            "reverse",
            "--input",
            "AGENT_fix.py",
            "--output",
            "agent-compat.py",
            "--dict",
            "mapping.json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    if Path("agent-compat.py").read_bytes() != generated_expected:
        raise RuntimeError("Compatibility reverse lost newly generated source")
    subprocess.run([sys.executable, "agent-compat.py"], check=True, timeout=10)
    Path("old-original.txt").write_bytes(b"alpha beta\r\n")
    Path("old-encoded.txt").write_bytes(b"beta __SCM_LIT_beta__\r\n")
    Path("old-ledger.json").write_text(
        json.dumps(
            {
                "original_file": "old-original.txt",
                "track_literals": ["beta"],
                "timestamp": "2024-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    legacy_args = (
        "migrate-ledger",
        "old-encoded.txt",
        "--original",
        "old-original.txt",
        "--ledger",
        "old-ledger.json",
        "--dict",
        "mapping.json",
    )
    migrated_review = run(*legacy_args)
    migrated = run(
        *legacy_args, "--apply", "--review-sha256", migrated_review["review_sha256"]
    )
    run("verify", migrated["output"])
    decoded_migration = run("decode", migrated["output"])
    if (
        Path(decoded_migration["output"]).read_bytes()
        != Path("old-original.txt").read_bytes()
    ):
        raise RuntimeError("Installed migration lost original bytes")
    translation_results = {
        "cli_conflict_count": conflict["conflict_count"],
        "cli_review_sha256": review["review_sha256"],
        "cli_restoration": restored_verification,
        "restored_python_executed": True,
        "retained_inputs_unchanged": True,
        "legacy_migration": migrated,
        "normal_pipeline": {
            "edited": normal_edit,
            "generated": normal_generated,
            "compatibility": json.loads(compatibility.stderr),
            "strict_edit_refused": True,
            "restored_programs_executed": 3,
        },
        "mcp": {},
    }
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
    workshop = Path("workshop-fixture")
    run("workshop-init", "--workshop", str(workshop))
    scrape = workshop / "scrape"
    readiness = run("workshop-check", "--workshop", str(workshop))
    if not readiness["ok"] or readiness["deployment_qualified"]:
        raise RuntimeError("Installed pipeline readiness contract failed")
    candidate = examples.candidate(workshop, "ExampleA", ["red hair"])
    for script, arguments in [
        ("promote.py", ["--candidate", candidate, "--dry-run"]),
        ("promote.py", ["--candidate", candidate]),
        ("audit_matrix.py", ["--apply"]),
        ("promote.py", ["--quarantine", "ExampleA", "--reason", "installed fixture"]),
    ]:
        result = run_script(scrape, Path(sys.executable), script, *arguments)
        if result["returncode"] != 0:
            raise RuntimeError(f"Installed native pipeline failed: {result}")

    # Leave a real incomplete journal for the installed workshop stdio server.
    examples.candidate(workshop, "ExampleB", ["red hair"])
    prepared = plans.prepare_promotion(workshop, "candidates/ExampleB.json")
    writer = ops.atomic_write

    def fail_ledger(path: Path, data: bytes, **kwargs) -> None:
        if path == workshop.absolute() / ops.LEDGER:
            raise OSError("Installed reader qualification fault")
        writer(path, data, **kwargs)

    operation_id = ""
    with patch.object(ops, "atomic_write", fail_ledger):
        try:
            plans.apply_plan(
                workshop,
                prepared["operation_id"],
                prepared["plan_sha256"],
                dry_run=False,
            )
        except ops.PendingOperation as exc:
            operation_id = exc.operation_id
    if operation_id != prepared["operation_id"]:
        raise RuntimeError("Installed failure injection did not leave a journal")

    diagnostic_results = []

    async def smoke_diagnostics(expected: str) -> None:
        for module in ("workshop.mcp.workshop_mcp", "workshop.scrape.bamb5"):
            transport = StdioTransport(
                command=sys.executable,
                args=["-c", f"from {module} import mcp; mcp.run()"],
                cwd=str(Path.cwd()),
                env={"VIGILES_WORKSHOP_ROOT": str(workshop.absolute())},
            )
            before = {
                str(p): sha256(p.read_bytes())
                for p in workshop.rglob("*")
                if p.is_file()
            }
            async with Client(transport) as client:
                result = await client.call_tool(
                    "diagnose_recovery", {"operation_id": operation_id}
                )
                content = result.content[0]
                if result.is_error or content.type != "text":
                    raise RuntimeError("Installed recovery diagnostic tool failed")
                execution = json.loads(content.text)
                if execution["returncode"] != 0 or execution["stdout_truncated"]:
                    raise RuntimeError(
                        f"Installed diagnostic output failed: {execution}"
                    )
                report = json.loads(execution["stdout"])
                if report["status"] != expected or report["recovery_ready"] != (
                    expected == "recoverable"
                ):
                    raise RuntimeError(f"Installed diagnostic state mismatch: {report}")
                after = {
                    str(p): sha256(p.read_bytes())
                    for p in workshop.rglob("*")
                    if p.is_file()
                }
                if before != after:
                    raise RuntimeError("Installed diagnostics changed workshop files")
                diagnostic_results.append(
                    {
                        "module": module,
                        "expected": expected,
                        "report": report,
                        "read_only": True,
                    }
                )

    asyncio.run(asyncio.wait_for(smoke_diagnostics("recoverable"), timeout=60))
    # Controlled fixture edits prove that both installed interfaces expose all
    # conflicts. Restore only these synthetic bytes to continue recovery testing.
    conflict_files = [workshop / ops.LEDGER, workshop / "prod/model-ExampleB.bin"]
    original_fixture_bytes = {path: path.read_bytes() for path in conflict_files}
    for path in conflict_files:
        path.write_bytes(b"controlled installed conflict")
    asyncio.run(asyncio.wait_for(smoke_diagnostics("conflict"), timeout=60))
    for path, raw in original_fixture_bytes.items():
        path.write_bytes(raw)

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
                or json.loads(content.text)["entries"] != 415
            ):
                raise RuntimeError("Installed MCP tool call failed")

            async def call(name: str, arguments: dict) -> dict:
                response = await client.call_tool(name, arguments)
                content = response.content[0]
                if response.is_error or content.type != "text":
                    raise RuntimeError("Installed translation MCP call failed")
                return json.loads(content.text)

            arguments = {
                "source": "agent-original_safe.py",
                "original": "agent-original.py",
                "output": "agent-mcp-restored.py",
            }
            conflict = await call("preview_restoration", arguments)
            if conflict["status"] != "conflict" or "plan_id" in conflict:
                raise RuntimeError("Installed MCP issued a token for unresolved edits")
            review = await call(
                "preview_restoration", {**arguments, "terms": "translate"}
            )
            saved = await call("apply_preview", {"plan_id": review["plan_id"]})
            verified = await call("verify_restored_file", {"source": saved["output"]})
            await asyncio.to_thread(
                subprocess.run,
                [sys.executable, saved["output"]],
                check=True,
                timeout=10,
            )
            if Path(saved["output"]).read_bytes() != expected_program:
                raise RuntimeError("Installed MCP restored unexpected source")
            replay = await client.call_tool(
                "apply_preview", {"plan_id": review["plan_id"]}, raise_on_error=False
            )
            if not replay.is_error:
                raise RuntimeError("Installed restoration preview replay was accepted")
            legacy = await call(
                "preview_migration",
                {
                    "source": "old-encoded.txt",
                    "original": "old-original.txt",
                    "ledger": "old-ledger.json",
                    "dictionary": "mapping.json",
                    "output": "old-mcp-migrated.txt",
                },
            )
            applied = await call("apply_preview", {"plan_id": legacy["plan_id"]})
            migration_verified = await call(
                "verify_file", {"source": applied["output"]}
            )
            normal_returns = []
            for source, output, options, expected in (
                (
                    "agent-original_safe.py",
                    "agent-mcp-normal-edit.py",
                    {},
                    expected_program,
                ),
                (
                    "AGENT_fix.py",
                    "agent-mcp-generated.py",
                    {"ledger": ".agent-original_safe.py.scm_ledger.json"},
                    generated_expected,
                ),
                (
                    "AGENT_fix.py",
                    "agent-mcp-dictionary.py",
                    {"dictionary": "mapping.json"},
                    generated_expected,
                ),
            ):
                normal_preview = await call(
                    "preview_file",
                    {
                        "source": source,
                        "output": output,
                        "reverse": True,
                        **options,
                    },
                )
                normal_saved = await call(
                    "apply_preview", {"plan_id": normal_preview["plan_id"]}
                )
                if Path(output).read_bytes() != expected:
                    raise RuntimeError(
                        "Installed normal MCP return changed expected source"
                    )
                await asyncio.to_thread(
                    subprocess.run, [sys.executable, output], check=True, timeout=10
                )
                normal_returns.append(normal_saved)
            translation_results["mcp"] = {
                "restoration": verified,
                "restored_python_executed": True,
                "replay_refused": True,
                "migration": migration_verified,
                "normal_returns": normal_returns,
                "normal_programs_executed": 3,
            }

    asyncio.run(asyncio.wait_for(smoke_mcp(), timeout=30))

    # Restore the original agent boundary itself: the agent reads encoded source
    # and submits neutral content, while only the local files contain domain terms.
    agent_root = Path("cipher-agent")
    agent_root.mkdir()
    (agent_root / "mapping.json").write_text('{"alpha":"beta"}', encoding="utf-8")
    (agent_root / "node.py").write_bytes(program)
    cipher_command = Path(sys.executable).with_name(
        "scm-cipher.exe" if sys.platform == "win32" else "scm-cipher"
    )
    encoded_stream = subprocess.run(
        [
            str(cipher_command),
            "--root",
            str(agent_root),
            "--mode",
            "forward",
            "--input",
            "-",
            "--output",
            "-",
            "--dict",
            "mapping.json",
        ],
        input="alpha beta",
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    if encoded_stream != "beta __SCM_LIT_beta__":
        raise RuntimeError("Installed cipher entry point did not encode the stream")

    async def smoke_agent_cipher() -> dict:
        transport = StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "bamboozle",
                "--root",
                str(agent_root.resolve()),
                "serve-agent",
                "--dict",
                "mapping.json",
            ],
            cwd=str(Path.cwd()),
        )
        async with Client(transport) as client:
            if {tool.name for tool in await client.list_tools()} != {
                "read_safe_file",
                "write_safe_file",
            }:
                raise RuntimeError(
                    "Agent server exposed tools outside the cipher file interface"
                )

            async def call(name: str, arguments: dict) -> dict:
                result = await client.call_tool(name, arguments)
                block = result.content[0]
                if result.is_error or block.type != "text":
                    raise RuntimeError("Agent cipher tool call failed")
                return json.loads(block.text)

            read = await call("read_safe_file", {"file_path": "node.py"})
            if "alpha" in json.dumps(read) or "__SCM_LIT_beta__" not in read["content"]:
                raise RuntimeError("Agent read did not present the encoded vocabulary")
            content = read["content"].replace("x + 1", "x * 2").replace("== 4", "== 6")
            edited = await call(
                "write_safe_file", {"file_path": "node.py", "content": content}
            )
            generated = await call(
                "write_safe_file",
                {
                    "file_path": "generated.py",
                    "content": "from node import beta\nassert beta(4) == 8\n",
                },
            )
            if "alpha" in json.dumps([edited, generated]):
                raise RuntimeError("Cipher write response returned decoded source")
            if (agent_root / "node.py").read_bytes() != expected_program:
                raise RuntimeError(
                    "Agent edit was not decoded into the authoritative source file"
                )
            backup = agent_root / edited["history"] / "original.bin"
            if backup.read_bytes() != program:
                raise RuntimeError("Agent edit did not preserve the prior source bytes")
            await asyncio.to_thread(
                subprocess.run,
                [sys.executable, str(agent_root / "generated.py")],
                check=True,
                timeout=10,
            )
            private = await client.call_tool(
                "read_safe_file", {"file_path": "mapping.json"}, raise_on_error=False
            )
            history = await client.call_tool(
                "read_safe_file",
                {"file_path": edited["history"] + "/original.bin"},
                raise_on_error=False,
            )
            if not private.is_error or not history.is_error:
                raise RuntimeError(
                    "Agent cipher interface exposed private reverse data"
                )
            await call("read_safe_file", {"file_path": "node.py"})
            changed = expected_program + b"# operator edit\r\n"
            (agent_root / "node.py").write_bytes(changed)
            stale = await client.call_tool(
                "write_safe_file",
                {"file_path": "node.py", "content": content},
                raise_on_error=False,
            )
            if not stale.is_error or (agent_root / "node.py").read_bytes() != changed:
                raise RuntimeError("Agent write overwrote a concurrent source edit")
            return {
                "edited": edited,
                "generated": generated,
                "agent_received_encoded_content_only": True,
                "private_mapping_and_history_refused": True,
                "prior_source_sha256": sha256(backup.read_bytes()),
                "restored_two_module_program_executed": True,
                "stale_write_refused": True,
            }

    agent_results = asyncio.run(asyncio.wait_for(smoke_agent_cipher(), timeout=30))

    async def smoke_original_agent_command() -> None:
        command = Path(sys.executable).with_name(
            "scm-mcp.exe" if sys.platform == "win32" else "scm-mcp"
        )
        async with Client(
            StdioTransport(
                command=str(command),
                args=["--root", str(agent_root.resolve()), "--dict", "mapping.json"],
                cwd=str(Path.cwd()),
            )
        ) as client:
            read = await client.call_tool("read_safe_file", {"file_path": "node.py"})
            block = read.content[0]
            if read.is_error or block.type != "text" or "alpha" in block.text:
                raise RuntimeError(
                    "Installed scm-mcp command did not serve encoded source"
                )

    asyncio.run(asyncio.wait_for(smoke_original_agent_command(), timeout=30))

    async def smoke_workshop() -> None:
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "workshop.mcp.workshop_mcp"],
            cwd=str(Path.cwd()),
            env={"VIGILES_WORKSHOP_ROOT": str(workshop.absolute())},
        )
        async with Client(transport) as client:
            failed = await client.call_tool("query_manifest", {}, raise_on_error=False)
            if not failed.is_error:
                raise RuntimeError("Installed workshop exposed incomplete publication")
            status = await client.call_tool("pipeline_status", {"rebuild_index": True})
            if "blocked_pending_recovery" not in str(status.content):
                raise RuntimeError("Installed workshop did not block index rebuild")
            pending_inventory = await client.call_tool("list_plans", {})
            pending_content = pending_inventory.content[0]
            if pending_content.type != "text":
                raise RuntimeError("Installed pending inventory did not return text")
            pending_report = json.loads(pending_content.text)
            if pending_report["returncode"] != 0 or pending_report["stdout_truncated"]:
                raise RuntimeError("Installed pending inventory failed")
            if (
                json.loads(pending_report["stdout"])["items"][0]["status"]
                != "recovery_required"
            ):
                raise RuntimeError("Installed inventory hid the pending plan")
            recovered = run_script(
                scrape, Path(sys.executable), "promote.py", "--recover", operation_id
            )
            if recovered["returncode"] != 0:
                raise RuntimeError(f"Installed reader recovery failed: {recovered}")
            if (
                json.loads(recovered["stdout"])["journal_sha256"]
                != prepared["plan_sha256"]
            ):
                raise RuntimeError("Installed recovery changed the reviewed plan")
            query = await client.call_tool("lora_who", {"trigger": "red hair"})
            content = query.content[0]
            if content.type != "text" or json.loads(content.text)["loras"] != [
                "ExampleB"
            ]:
                raise RuntimeError("Installed workshop did not refresh after recovery")

    asyncio.run(asyncio.wait_for(smoke_workshop(), timeout=30))
    asyncio.run(asyncio.wait_for(smoke_diagnostics("already_completed"), timeout=60))
    examples.candidate(workshop, "ExampleC", ["neon"])

    async def smoke_plans(module: str) -> dict:
        transport = StdioTransport(
            command=sys.executable,
            args=["-c", f"from {module} import mcp; mcp.run()"],
            cwd=str(Path.cwd()),
            env={"VIGILES_WORKSHOP_ROOT": str(workshop.absolute())},
        )
        async with Client(transport) as client:

            async def call(name: str, arguments: dict, code: int = 0) -> dict:
                result = await client.call_tool(name, arguments)
                content = result.content[0]
                if content.type != "text":
                    raise RuntimeError("Installed plan tool did not return JSON text")
                report = json.loads(content.text)
                if report["returncode"] != code or report["stdout_truncated"]:
                    raise RuntimeError(f"Installed plan tool failed: {report}")
                return json.loads(report["stdout"])

            stale = await call(
                "prepare_promotion", {"candidate": "candidates/ExampleC.json"}
            )
            review = await call(
                "prepare_promotion", {"candidate": "candidates/ExampleC.json"}
            )
            identity = {
                "plan_id": review["operation_id"],
                "plan_sha256": review["plan_sha256"],
            }
            inspected = await call("inspect_plan", {"plan_id": identity["plan_id"]})
            if (
                not inspected["applicable"]
                or inspected["plan_sha256"] != identity["plan_sha256"]
            ):
                raise RuntimeError("Installed plan inspection mismatch")
            preview = await call("apply_plan", identity)
            if preview["status"] != "plan_preview":
                raise RuntimeError("Installed plan tool default is not preview")
            saved = await call("apply_plan", {**identity, "dry_run": False})
            if saved["journal_sha256"] != identity["plan_sha256"]:
                raise RuntimeError("Installed application changed the reviewed plan")
            replay = await call("apply_plan", {**identity, "dry_run": False}, code=1)
            if "applied" not in replay["error"]:
                raise RuntimeError("Installed application did not reject replay")
            quarantine = await call(
                "prepare_quarantine",
                {"name": "ExampleC", "reason": "installed fixture"},
            )
            removed = await call(
                "apply_plan",
                {
                    "plan_id": quarantine["operation_id"],
                    "plan_sha256": quarantine["plan_sha256"],
                    "dry_run": False,
                },
            )
            if removed["journal_sha256"] != quarantine["plan_sha256"]:
                raise RuntimeError("Installed quarantine changed the reviewed plan")
            unused = await call(
                "prepare_quarantine",
                {"name": "ExampleB", "reason": "cancelled fixture"},
            )
            cancelled = await call(
                "cancel_plan",
                {
                    "plan_id": unused["operation_id"],
                    "plan_sha256": unused["plan_sha256"],
                },
            )
            if (
                cancelled["status"] != "cancelled"
                or not (workshop / "prod/model-ExampleB.bin").exists()
            ):
                raise RuntimeError("Installed cancellation changed production assets")
            retained = await call(
                "prepare_quarantine",
                {"name": "ExampleB", "reason": "inventory fixture"},
            )
            (scrape / "prepared/broken-fixture.tmp").write_bytes(
                b"retained incomplete fixture"
            )

            def inventory_files() -> dict:
                return {
                    str(path.relative_to(workshop)): sha256(path.read_bytes())
                    for path in workshop.rglob("*")
                    if path.is_file()
                }

            before_inventory = inventory_files()
            rows = []
            cursor = None
            page_count = 0
            while True:
                page = await call("list_plans", {"limit": 2, "cursor": cursor})
                if page["asset_hashes_checked"] or page["apply_preflight"]:
                    raise RuntimeError("Inventory incorrectly claimed apply readiness")
                rows.extend(page["items"])
                page_count += 1
                cursor = page["next_cursor"]
                if cursor is None:
                    break
                if page_count > 20:
                    raise RuntimeError("Inventory paging did not finish")
            if inventory_files() != before_inventory:
                raise RuntimeError("Installed inventory mutated workshop files")
            observed = {row.get("operation_id"): row["status"] for row in rows}
            expected = {
                stale["operation_id"]: "stale",
                review["operation_id"]: "applied",
                unused["operation_id"]: "cancelled",
                retained["operation_id"]: "prepared",
            }
            if (
                any(observed.get(key) != value for key, value in expected.items())
                or "corrupt" not in observed.values()
            ):
                raise RuntimeError("Installed inventory states are incomplete")
            if len({row["file"] for row in rows}) != len(rows):
                raise RuntimeError("Installed inventory repeated a record")
            return {
                "module": module,
                "status": "passed",
                "promotion": saved,
                "quarantine": removed,
                "cancelled": cancelled,
                "inventory": {
                    "pages": page_count,
                    "records": len(rows),
                    "states": sorted(set(observed.values())),
                    "read_only": True,
                },
            }

    plan_results = [
        asyncio.run(asyncio.wait_for(smoke_plans(module), timeout=60))
        for module in (
            "workshop.mcp.workshop_mcp",
            "workshop.scrape.bamb5",
        )
    ]
    capture_results = smoke_capture()
    profanity_results = smoke_profanity()
    project_view_results = smoke_project_views()
    document_format_results = smoke_document_formats()
    lexicon_results = smoke_lexicon()
    fixture_results = smoke_fixtures()
    return_results = runpy.run_path(str(Path(__file__).with_name("return_smoke.py")))[
        "run"
    ]()
    escape_results = runpy.run_path(str(Path(__file__).with_name("escape_smoke.py")))[
        "run"
    ]()
    if not Path(escape_results["marker_module"]).is_relative_to(Path(sys.prefix)):
        raise RuntimeError(
            "Marker implementation was not imported from the installed wheel"
        )
    python_results = runpy.run_path(str(Path(__file__).with_name("python_smoke.py")))[
        "run"
    ]()
    if any(
        not Path(python_results[name]).is_relative_to(Path(sys.prefix))
        for name in ("source_module", "project_module")
    ):
        raise RuntimeError(
            "Python source/project adapters were not imported from the installed wheel"
        )
    print(
        json.dumps(
            {
                "status": "passed",
                "version": bamboozle.__version__,
                "package_file": str(package_file),
                "capture_results": capture_results,
                "plan_results": plan_results,
                "diagnostic_results": diagnostic_results,
                "translation_results": translation_results,
                "agent_cipher_results": agent_results,
                "profanity_results": profanity_results,
                "project_view_results": project_view_results,
                "document_format_results": document_format_results,
                "lexicon_results": lexicon_results,
                "fixture_results": fixture_results,
                "return_results": return_results,
                "escape_results": escape_results,
                "python_results": python_results,
                "reviewed_plan_recovery": {
                    "operation_id": operation_id,
                    "plan_sha256": prepared["plan_sha256"],
                },
                "checks": [
                    "python_dependency_application_cli_stdio_edits_new_capacity_scheduler_300_workload_runs_neutral_and_restored_and_encrypted_return",
                    "versioned_prefix_escapes_cli_operator_and_source_agent_stdio_executed_stream_parser_views_and_encrypted_returns",
                    "saved_context_return_diagnostics_cli_stdio_paged_views_encrypted_edits_and_executed_returned_programs",
                    "generated_template_fixtures_public_invariant_contracts_cases_and_returned_catalog_execution_cli_stdio",
                    "operator_lexicon_authoring_compilation_provenance_cli_stdio_structured_edits_and_executed_multi_module_pipeline",
                    "bundled_resources",
                    "structured_markdown_json_saved_context_cli_stdio_views_executed_code_and_separate_encryption_composition",
                    "project_view_cli_stdio_scoped_export_reviewed_multi_file_return_and_explicit_recovery",
                    "cli_roundtrip",
                    "original_cipher_cli_and_agent_stdio_encoded_read_decoded_write_executed_program",
                    "normal_agent_edit_and_generated_source_cli_compatibility_and_stdio_execution",
                    "edited_source_restoration_conflicts_provenance_and_execution_cli_and_stdio",
                    "historical_ledger_migration_cli_and_stdio",
                    "ledger_verify",
                    "tessera_roundtrip",
                    "baseline_audit",
                    "mcp_stdio_tool_call",
                    "workshop_readiness_without_execution",
                    "native_promotion_audit_quarantine",
                    "workshop_stdio_pending_read_rejection_and_recovery",
                    "bounded_capture_success_failure_timeout",
                    "both_workshop_stdio_reviewed_plan_prepare_inspect_apply_replay_cancel",
                    "installed_reviewed_plan_fault_recovery",
                    "installed_inventory_pending_recovery_and_both_stdio_paged_states",
                    "both_workshop_stdio_recovery_diagnostics_conflicts_and_completed_history",
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
