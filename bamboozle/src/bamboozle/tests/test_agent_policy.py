"""Actual source-agent document handling, reconnects and package generation."""

import json
import runpy
from pathlib import Path

import pytest

from bamboozle.agent_checks import CHECK_TOOLS
from bamboozle.agent_development import AGENT_TOOLS
from bamboozle.agent_files import AgentFiles
from bamboozle.agent_policy import AgentPolicy
from bamboozle.dictionary import Dictionary
from bamboozle.utils import json_bytes
from bamboozle.workspace import Workspace


@pytest.fixture
def configured(tmp_path):
    rules = {"format": "python", "identifiers": ["task"], "modules": ["module"]}
    document = {
        "schema_version": 2,
        "files": {
            "module.py": {"policy": "translate", "document": rules},
            "config.json": {
                "policy": "translate",
                "document": {"format": "json", "pointers": ["/selected"]},
            },
            "notes.md": {"policy": "translate", "document": {"format": "markdown"}},
            "private.txt": {"policy": "exclude"},
        },
        "new_files": {"package": {"policy": "translate", "document": rules}},
    }
    (tmp_path / "source.agent-policy.json").write_bytes(json_bytes(document))
    (tmp_path / "module.py").write_bytes(
        b'\xef\xbb\xbffrom pathlib import Path\r\ndef task(x):\r\n    return x + 1\r\nLABEL = "task"\r\n# task stays literal\r\n'
    )
    (tmp_path / "config.json").write_bytes(
        b'{"selected": "task", "task": "task", "other": "stage"}\r\n'
    )
    (tmp_path / "notes.md").write_bytes(b"# task\r\n`task` stays literal.\r\n")
    workspace = Workspace(tmp_path)
    dictionary = Dictionary({"task": "stage", "path": "location"})
    return AgentFiles(
        workspace, dictionary, policy=AgentPolicy(workspace, "source.agent-policy.json")
    )


def test_direct_read_preserves_external_import_strings_comments_and_line_endings(
    configured,
):
    read = configured.read("module.py")
    assert read["document_format"] == "python"
    assert "from pathlib import Path\r\n" in read["content"]
    assert "def stage(x)" in read["content"]
    assert 'LABEL = "task"\r\n# task stays literal' in read["content"]
    executable = configured.workspace.root / "neutral-check.py"
    executable.write_bytes(read["content"].encode("utf-8"))
    program = runpy.run_path(str(executable))
    assert program["stage"](3) == 4
    saved = configured.write("module.py", read["content"].replace("x + 1", "x * 2"))
    original = (
        configured.workspace.root / saved["history"] / "original.bin"
    ).read_bytes()
    assert configured.workspace.read("module.py") == original.replace(
        b"x + 1", b"x * 2"
    )
    assert saved["receipt_saved"]


def test_direct_json_and_markdown_use_the_existing_adapters(configured):
    read = configured.read("config.json")
    assert json.loads(read["content"]) == {
        "selected": "stage",
        "task": "task",
        "other": "stage",
    }
    configured.write(
        "config.json", read["content"].replace('"other": "stage"', '"other": "literal"')
    )
    assert (
        configured.workspace.read("config.json")
        == b'{"selected": "task", "task": "task", "other": "literal"}\r\n'
    )
    note = configured.read("notes.md")
    assert note["content"] == "# stage\r\n`task` stays literal.\r\n"
    configured.write("notes.md", note["content"] + "stage added.\r\n")
    assert configured.workspace.read("notes.md").endswith(b"task added.\r\n")


def test_restart_pins_dictionary_and_format_context(configured):
    read = configured.read("module.py")
    restarted = AgentFiles(
        configured.workspace, configured.dictionary, policy=configured.policy
    )
    with pytest.raises(ValueError, match="context_sha256"):
        restarted.write("module.py", read["content"], read["source_sha256"])
    changed = AgentFiles(
        configured.workspace, Dictionary({"task": "job"}), policy=configured.policy
    )
    with pytest.raises(ValueError, match="context changed"):
        changed.write(
            "module.py", read["content"], read["source_sha256"], read["context_sha256"]
        )
    assert restarted.write(
        "module.py", read["content"], read["source_sha256"], read["context_sha256"]
    )["receipt_saved"]


def test_new_nested_modules_are_decoded_and_invalid_python_is_not_published(configured):
    saved = configured.write(
        "package/submodule/new.py", "from module import stage\nassert stage(3) == 4\n"
    )
    assert saved["receipt_saved"] and not saved["backup_saved"]
    assert (
        configured.workspace.read("package/submodule/new.py")
        == b"from module import task\nassert task(3) == 4\n"
    )
    with pytest.raises(ValueError, match="syntax"):
        configured.write("package/bad.py", "def stage(:\n")
    assert not (configured.workspace.root / "package/bad.py").exists()
    assert "import stage" in configured.read("package/submodule/new.py")["content"]


@pytest.mark.parametrize(
    "name",
    ["private.txt", "unselected.py", "source.agent-policy.json", "package/secret.key"],
)
def test_private_or_unselected_files_remain_unavailable(configured, name):
    with pytest.raises(ValueError):
        configured.read(name)
    with pytest.raises(ValueError):
        configured.write(name, "stage")


def test_policy_changes_require_fresh_server_context(configured):
    read = configured.read("module.py")
    assert configured.policy is not None
    configured.policy.path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="policy changed"):
        configured.write("module.py", read["content"])
    assert b"def task" in configured.workspace.read("module.py")


@pytest.mark.parametrize("name", ["PRIVATE.txt", "MODULE.py", "Package/new.py"])
def test_case_aliases_cannot_bypass_explicit_file_or_directory_policy(configured, name):
    with pytest.raises(ValueError, match="exact"):
        configured.write(name, "stage")


def test_vandal_routes_the_private_policy_to_source_tools(configured, monkeypatch):
    root = Path(__file__).parents[3]
    function = runpy.run_path(str(root / "scripts/vandal.py"))["context"]
    for name in (
        "pyproject.toml",
        ".mivim/vigiles-vandal_0.2.md",
        ".vigiles/skills/vigiles-vandal/SKILL.md",
    ):
        target = configured.workspace.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    monkeypatch.setitem(function.__globals__, "ROOT", configured.workspace.root)
    report = function("agent", policy="source.agent-policy.json")
    assert report["mcp_config"]["mcpServers"]["bamboozle_agent"]["args"][-2:] == [
        "--policy",
        str(configured.policy.path),
    ]
    assert report["source_policy"]["formats"] == ["json", "markdown", "python"]
    assert "identifiers" not in json.dumps(report["source_policy"])
    with pytest.raises(ValueError, match="only to agent"):
        function("operator", policy="source.agent-policy.json")


def test_source_agent_complete_application_through_real_stdio(tmp_path):
    script = Path(__file__).parents[3] / "scripts/source_agent_smoke.py"
    result = runpy.run_path(str(script))["run"](tmp_path / "direct-source")
    assert result["neutral_benchmark"] == result["restored_benchmark"]
    assert result["restored_benchmark"]["scheduler_executions"] == 300
    assert set(result["agent_tools"]) == AGENT_TOOLS | CHECK_TOOLS
    assert result["failed_baseline_check"]["status"] == "failed"
    assert result["agent_application_checks"]["workloads"]["status"] == "passed"
