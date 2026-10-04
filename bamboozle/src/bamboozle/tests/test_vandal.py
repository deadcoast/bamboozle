"""Repository workflow routing is separate from the packaged Bamboozle engine."""

import asyncio
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from bamboozle.dictionary import Dictionary
from bamboozle.project_views import export_view
from bamboozle.utils import json_bytes
from bamboozle.workspace import Workspace

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/vandal.py"


@pytest.mark.parametrize("mode", ["agent", "operator"])
def test_generated_context_works_from_other_directory_and_exposes_its_actual_tools(
    tmp_path, mode
):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--mode", mode],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    context = json.loads(result.stdout)
    assert context["workflow"] == "vigiles-vandal_0.2"
    assert context["workflow_version"] != context["product_version"]
    assert not context["configuration_written"]
    assert list(tmp_path.iterdir()) == []
    connection = context["mcp_config"]["mcpServers"][f"bamboozle_{mode}"]
    assert len(context["mcp_config"]["mcpServers"]) == 1

    async def exercise():
        async with Client(StdioTransport(**connection, cwd=str(tmp_path))) as client:
            names = {tool.name for tool in await client.list_tools()}
            if mode == "agent":
                assert names == {"read_safe_file", "write_safe_file"}
                response = await client.call_tool(
                    "read_safe_file", {"file_path": "src/bamboozle/__init__.py"}
                )
            else:
                assert {
                    "preview_file",
                    "export_project_view",
                    "review_project_view",
                    "apply_project_view",
                    "validate_returned_file",
                } <= names
                response = await client.call_tool("dictionary_status", {})
            assert not response.is_error and response.content[0].type == "text"
            document = json.loads(response.content[0].text)
            if mode == "agent":
                assert document["status"] == "read"
            else:
                assert document["entries"] == 415
                assert (
                    context["return_validation"][
                        "standalone_check_grants_apply_authority"
                    ]
                    is False
                )

    asyncio.run(exercise())


def test_neutral_view_context_has_no_encoding_or_operator_connection(
    tmp_path, monkeypatch
):
    function = runpy.run_path(str(SCRIPT))["context"]
    monkeypatch.setitem(function.__globals__, "ROOT", tmp_path)
    for name in (
        "pyproject.toml",
        ".mivim/vigiles-vandal_0.2.md",
        ".vigiles/skills/vigiles-vandal/SKILL.md",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    (tmp_path / "source.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "policy.json").write_bytes(
        json_bytes(
            {
                "schema_version": 1,
                "files": {"source.txt": {"policy": "translate"}},
                "new_files": {},
            }
        )
    )
    exported = export_view(
        Workspace(tmp_path), "policy.json", "view", Dictionary({"alpha": "omega"})
    )
    result = function("view", exported["view_id"])
    assert result["workspace"] == str(tmp_path / "view")
    assert result["mcp_config"] == {"mcpServers": {}}
    assert result["marker_version"] == 2
    assert "dictionary" not in result and "private_state" not in result
    (tmp_path / "view/BAMBOOZLE_VIEW.md").write_bytes(b"changed")
    with pytest.raises(ValueError, match="contract changed"):
        function("view", exported["view_id"])


def test_workflow_refuses_foreign_repository_or_ambiguous_mode(tmp_path, monkeypatch):
    function = runpy.run_path(str(SCRIPT))["context"]
    with pytest.raises(ValueError, match="Choose"):
        function("operator", "1" * 32)
    with pytest.raises(ValueError, match="Choose"):
        function("view")
    monkeypatch.setitem(function.__globals__, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="unrelated"\nversion="0.4.5"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="this repository"):
        function("operator")
