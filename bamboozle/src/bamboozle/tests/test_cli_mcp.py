import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.client import CallToolResult

from bamboozle.cli import main
from bamboozle.dictionary import Dictionary
from bamboozle.mcp import create_server


def _report(result: CallToolResult) -> dict[str, Any]:
    content = result.content[0]
    assert content.type == "text"
    return json.loads(content.text)


def test_installed_cli_runs_from_another_directory(tmp_path):
    mapping = tmp_path / "mapping.json"
    mapping.write_text('{"alpha":"beta"}', encoding="utf-8")
    original = b"Alpha beta\r\n"
    (tmp_path / "source.txt").write_bytes(original)

    def run(*args):
        result = subprocess.run(
            [sys.executable, "-m", "bamboozle", "--root", str(tmp_path), *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(result.stdout)

    assert run("validate", "--dict", str(mapping))["entries"] == 1
    assert run("preview", "source.txt", "--dict", str(mapping))["status"] == "preview"
    assert not (tmp_path / "source_safe.txt").exists()
    assert run("encode", "source.txt", "--dict", str(mapping))["status"] == "saved"
    assert run("verify", "source_safe.txt")["status"] == "verified"
    assert run("decode", "source_safe.txt")["status"] == "saved"
    assert (tmp_path / "source_safe_ready.txt").read_bytes() == original
    assert run("keygen", "--key", "agent.key")["status"] == "key_created"
    assert (
        run(
            "seal",
            "source.txt",
            "-o",
            "packet.json",
            "--key",
            "agent.key",
            "--recipient",
            "test",
        )["status"]
        == "sealed"
    )
    assert (
        run(
            "open",
            "packet.json",
            "-o",
            "delivered.txt",
            "--key",
            "agent.key",
            "--recipient",
            "test",
        )["status"]
        == "opened"
    )
    assert (tmp_path / "delivered.txt").read_bytes() == original


def test_cli_audit_exit_code_and_bounded_watch(tmp_path, capsys):
    root = ["--root", str(tmp_path)]
    assert main([*root, "audit"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "unbaselined"
    assert main([*root, "baseline"]) == 0
    capsys.readouterr()
    assert main([*root, "watch", "--count", "1", "--interval", "0.1"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "clean"
    assert main([*root, "watch", "--count", "0"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_mcp_preview_apply_verify_and_replay(tmp_path):
    (tmp_path / "source.txt").write_text("alpha and beta", encoding="utf-8")
    server = create_server(tmp_path, Dictionary({"alpha": "beta"}))

    async def exercise():
        async with Client(server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert set(tools) == {
                "cipher_catalog",
                "preview_cipher_transform",
                "verify_cipher_transform",
                "pack_project_view",
                "open_project_exchange",
                "pack_project_return",
                "project_exchange_context",
                "dictionary_status",
                "preview_template_fixture",
                "validate_template_fixture",
                "export_dictionary_lexicon",
                "check_dictionary_lexicon",
                "query_dictionary_lexicon",
                "preview_lexicon_edit",
                "preview_lexicon_compilation",
                "verify_dictionary_compilation",
                "preview_file",
                "apply_preview",
                "verify_file",
                "audit_workspace",
                "preview_restoration",
                "preview_migration",
                "verify_restored_file",
                "validate_returned_file",
                "list_safe_files",
                "search_safe_files",
                "edit_safe_file",
                "read_safe_file",
                "write_safe_file",
                "scan_file",
                "export_project_view",
                "review_project_view",
                "apply_project_view",
                "project_view_status",
            }
            annotations = tools["preview_file"].annotations
            assert annotations is not None and annotations.read_only_hint
            result = await client.call_tool("preview_file", {"source": "source.txt"})
            report = _report(result)
            assert not (tmp_path / "source_safe.txt").exists()
            applied = await client.call_tool(
                "apply_preview", {"plan_id": report["plan_id"]}
            )
            assert _report(applied)["status"] == "saved"
            verified = await client.call_tool(
                "verify_file", {"source": "source_safe.txt"}
            )
            assert _report(verified)["status"] == "verified"
            replay = await client.call_tool(
                "apply_preview", {"plan_id": report["plan_id"]}, raise_on_error=False
            )
            assert replay.is_error
            escape = await client.call_tool(
                "preview_file", {"source": "../escape.txt"}, raise_on_error=False
            )
            assert escape.is_error

    asyncio.run(exercise())


def test_mcp_refuses_stale_preview_and_expired_capability(tmp_path, monkeypatch):
    (tmp_path / "source.txt").write_text("alpha", encoding="utf-8")
    server = create_server(tmp_path, Dictionary({"alpha": "beta"}))

    async def exercise():
        async with Client(server) as client:
            result = await client.call_tool("preview_file", {"source": "source.txt"})
            token = _report(result)["plan_id"]
            (tmp_path / "source.txt").write_text("changed", encoding="utf-8")
            rejected = await client.call_tool(
                "apply_preview", {"plan_id": token}, raise_on_error=False
            )
            assert rejected.is_error
            assert not (tmp_path / "source_safe.txt").exists()
            fresh = await client.call_tool("preview_file", {"source": "source.txt"})
            token = _report(fresh)["plan_id"]
            monkeypatch.setattr("bamboozle.mcp.PLAN_TTL", -1)
            expired = await client.call_tool(
                "apply_preview", {"plan_id": token}, raise_on_error=False
            )
            assert expired.is_error

    asyncio.run(exercise())


@pytest.mark.skipif(os.name != "nt", reason="Windows batch launchers")
def test_batch_launchers_use_the_installed_cli(tmp_path):
    source = Path(__file__).resolve().parents[2]
    term = next(iter(Dictionary.load()))
    original = (term + "\r\n").encode("utf-8")
    path = tmp_path / "batch sample.txt"
    path.write_bytes(original)
    environment = dict(
        os.environ,
        UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR", str(source.parent / ".uv-cache")),
    )
    for name, input_name in [
        ("ENCRYPT_FOR_AGENT.bat", "batch sample.txt"),
        ("DECRYPT_FROM_AGENT.bat", "batch sample_safe.txt"),
    ]:
        subprocess.run(
            ["cmd", "/c", str(source / name), str(tmp_path / input_name)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            env=environment,
        )
    assert (tmp_path / "batch sample_safe_ready.txt").read_bytes() == original
