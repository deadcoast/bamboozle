"""Agent navigation and editing must operate entirely in the working vocabulary."""

import asyncio
import json
import os
import runpy
import subprocess

import pytest
from fastmcp import Client

from bamboozle.agent_development import AgentDevelopment
from bamboozle.agent_files import HISTORY, AgentFiles
from bamboozle.agent_policy import AgentPolicy
from bamboozle.dictionary import Dictionary
from bamboozle.scm_mcp import create_server
from bamboozle.utils import json_bytes
from bamboozle.workspace import Workspace


@pytest.fixture
def development(tmp_path):
    rules = {"format": "python", "identifiers": ["task"], "modules": ["module"]}
    policy = {
        "schema_version": 2,
        "files": {
            "module.py": {"policy": "translate", "document": rules},
            "records.json": {
                "policy": "translate",
                "document": {"format": "json", "pointers": ["/text"]},
            },
            "private.txt": {"policy": "exclude"},
            "unreadable.py": {"policy": "translate", "document": rules},
        },
        "new_files": {"package": {"policy": "translate", "document": rules}},
    }
    (tmp_path / "source.agent-policy.json").write_bytes(json_bytes(policy))
    (tmp_path / "module.py").write_bytes(
        b'\xef\xbb\xbffrom pathlib import Path\r\ndef task(x):\r\n    return x + 1\r\nLABEL = "task"\r\n# untouched comment\r\n'
    )
    (tmp_path / "records.json").write_bytes(b'{"text":"task", "literal":"task"}\r\n')
    (tmp_path / "private.txt").write_bytes(b"confidential")
    (tmp_path / "unselected.txt").write_bytes(b"confidential")
    workspace = Workspace(tmp_path)
    files = AgentFiles(
        workspace,
        Dictionary({"task": "stage", "path": "location"}),
        policy=AgentPolicy(workspace, "source.agent-policy.json"),
    )
    return AgentDevelopment(files)


def edit_arguments(read):
    return {
        "expected_sha256": read["source_sha256"],
        "expected_context_sha256": read["context_sha256"],
        "expected_neutral_sha256": read["neutral_sha256"],
    }


def test_source_agent_exposes_a_complete_navigation_and_edit_surface(tmp_path):
    async def exercise():
        async with Client(create_server(tmp_path)) as client:
            assert {tool.name for tool in await client.list_tools()} == {
                "list_safe_files",
                "search_safe_files",
                "read_safe_file",
                "edit_safe_file",
                "write_safe_file",
            }

    asyncio.run(exercise())


def test_discovery_search_excerpt_edit_and_new_package_execute(development):
    listed = development.list_files(limit=1)
    assert listed["total"] == 2
    assert listed["generated_directories"]["package"]["identifiers"] == ["stage"]
    next_page = development.list_files(
        offset=listed["next_offset"], expected_snapshot=listed["snapshot_sha256"]
    )
    assert next_page["results"][0]["file_path"] == "records.json"
    found = development.search("def stage(")
    assert found["complete"] and found["total"] == 1
    match = found["results"][0]
    assert match["file_path"] == "module.py" and match["line"] == 2
    assert "def task" not in json.dumps(found)
    excerpt = development.read("module.py", 2, 3)
    assert excerpt["partial"] and excerpt["total_lines"] == 5
    assert excerpt["content"] == "def stage(x):\r\n    return x + 1\r\n"
    assert excerpt["neutral_sha256"] == match["neutral_sha256"]
    assert excerpt["document_rules"]["identifiers"] == ["stage"]
    saved = development.edit(
        "module.py",
        [{"old_text": "x + 1", "new_text": "x * 2"}],
        **edit_arguments(match),
    )
    assert saved["edits_applied"] == 1 and saved["receipt_saved"]
    root = development.files.workspace.root
    assert (root / "module.py").read_bytes() == (
        root / saved["history"] / "original.bin"
    ).read_bytes().replace(b"x + 1", b"x * 2")
    assert runpy.run_path(str(root / "module.py"))["task"](7) == 14
    development.files.write(
        "package/nested/new.py", "from module import stage\nresult = stage(7)\n"
    )
    assert "from module import task" in (root / "package/nested/new.py").read_text()
    assert development.list_files("package")["total"] == 1
    assert development.search("stage", "package")["total"] == 2


def test_selected_json_edits_preserve_literal_fields(development):
    read = development.read("records.json")
    development.edit(
        "records.json",
        [{"old_text": '"stage"', "new_text": '"stage added"'}],
        **edit_arguments(read),
    )
    assert (
        development.files.workspace.read("records.json")
        == b'{"text":"task added", "literal":"task"}\r\n'
    )


@pytest.mark.parametrize(
    "field", ["expected_sha256", "expected_context_sha256", "expected_neutral_sha256"]
)
def test_all_three_context_hashes_are_required_for_edits(development, field):
    read = development.read("module.py")
    arguments = edit_arguments(read)
    arguments[field] = "0" * 64
    with pytest.raises(ValueError, match="changed"):
        development.edit(
            "module.py", [{"old_text": "x + 1", "new_text": "0"}], **arguments
        )
    assert not (development.files.workspace.root / HISTORY).exists()


@pytest.mark.parametrize(
    "edits, message",
    [
        ([{"old_text": "x", "new_text": "y"}], "ambiguous"),
        ([{"old_text": "x", "new_text": "y", "occurrence": 3}], "does not match"),
        ([{"old_text": "absent", "new_text": "y"}], "does not match"),
        (
            [
                {"old_text": "x + 1", "new_text": "0"},
                {"old_text": "+ 1", "new_text": "- 1"},
            ],
            "overlap",
        ),
        ([{"old_text": "x + 1", "new_text": "x + 1"}], "do not change"),
        ([{"old_text": "", "new_text": "y"}], "nonempty"),
        ([{"old_text": "x", "new_text": "y", "occurrence": True}], "integer"),
        ([{"old_text": "x", "new_text": "y", "unknown": 1}], "requires"),
        ([{"old_text": "x + 1", "new_text": "("}], "syntax"),
    ],
)
def test_invalid_edits_publish_neither_source_nor_history(development, edits, message):
    before = development.files.workspace.read("module.py")
    read = development.read("module.py")
    with pytest.raises(ValueError, match=message):
        development.edit("module.py", edits, **edit_arguments(read))
    assert development.files.workspace.read("module.py") == before
    assert not (development.files.workspace.root / HISTORY).exists()


def test_repeated_and_adjacent_edits_use_the_original_snapshot(development):
    read = development.read("module.py")
    development.edit(
        "module.py",
        [
            {"old_text": "x", "new_text": "value", "occurrence": 1},
            {"old_text": "x", "new_text": "value", "occurrence": 2},
            {"old_text": " + ", "new_text": " * "},
            {"old_text": "1", "new_text": "3"},
        ],
        **edit_arguments(read),
    )
    result = runpy.run_path(str(development.files.workspace.root / "module.py"))
    assert result["task"](5) == 15


def test_edits_survive_reconnect_without_private_mapping_in_response(development):
    read = development.read("module.py")
    old = development.files
    restarted = AgentDevelopment(
        AgentFiles(old.workspace, old.dictionary, policy=old.policy)
    )
    saved = restarted.edit(
        "module.py",
        [{"old_text": "x + 1", "new_text": "x * 3"}],
        **edit_arguments(read),
    )
    assert saved["receipt_saved"] and "task" not in json.dumps(saved)


def test_failed_stale_edit_does_not_grant_an_implicit_write_of_unseen_source(
    development,
):
    read = development.read("module.py")
    path = development.files.workspace.resolve("module.py")
    changed = path.read_bytes().replace(b"x + 1", b"x * 9")
    path.write_bytes(changed)
    with pytest.raises(ValueError, match="changed"):
        development.edit(
            "module.py",
            [{"old_text": "x + 1", "new_text": "x * 2"}],
            **edit_arguments(read),
        )
    with pytest.raises(ValueError, match="Source changed"):
        development.files.write("module.py", read["content"])
    assert path.read_bytes() == changed


def test_search_does_not_evict_or_refresh_remembered_file_reads(development):
    development.read("module.py")
    before = dict(development.files.reads)
    development.search("stage")
    assert dict(development.files.reads) == before


def test_snapshot_pages_refuse_source_or_inventory_changes(development):
    found = development.search("stage", limit=1)
    development.files.workspace.resolve("module.py").write_bytes(
        b"def task(x):\n    return x\n"
    )
    with pytest.raises(ValueError, match="snapshot changed"):
        development.search(
            "stage", offset=1, expected_snapshot=found["snapshot_sha256"]
        )
    listed = development.list_files(limit=1)
    development.files.write("package/new.py", "value = 1\n")
    with pytest.raises(ValueError, match="snapshot changed"):
        development.list_files(offset=1, expected_snapshot=listed["snapshot_sha256"])


def test_unreadable_documents_are_reported_without_raw_diagnostics(development):
    (development.files.workspace.root / "unreadable.py").write_bytes(
        b"def highly_private_name(:\n"
    )
    found = development.search("stage", limit=1)
    assert not found["complete"] and found["total"] == 3
    last = development.search(
        "stage", offset=2, expected_snapshot=found["snapshot_sha256"]
    )
    assert last["results"][0]["kind"] == "unavailable"
    assert "highly_private_name" not in json.dumps(last)


def test_private_files_and_links_are_not_discovered_or_searched(development, tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    for name in (
        ".env",
        "auth.pem",
        "token.key",
        "map.dictionary.json",
        "source.agent-policy.json",
    ):
        (package / name).write_bytes(b"private content")
    (package / "allowed.py").write_bytes(b"def task():\n    return 1\n")
    private = package / ".vigiles"
    private.mkdir()
    (private / "source.py").write_bytes(b"private content")
    # Windows junctions exercise the actual link guard without symlink privilege.
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(package / "link"), str(tmp_path)],
            check=True,
            capture_output=True,
        )
    else:
        (package / "link").symlink_to(tmp_path, target_is_directory=True)
    assert [
        row["file_path"] for row in development.list_files("package")["results"]
    ] == ["package/allowed.py"]
    assert development.search("private")["total"] == 0
    with pytest.raises(ValueError, match="Private"):
        development.list_files("package/.vigiles")


def test_changed_policy_is_never_reported_as_empty_search(development):
    development.files.policy.path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="policy changed"):
        development.search("stage")


def test_limits_request_narrower_scope_instead_of_false_negative(
    development, monkeypatch
):
    import bamboozle.agent_development as module

    monkeypatch.setattr(module, "MAX_FILES", 1)
    with pytest.raises(ValueError, match="file limit"):
        development.list_files()
    monkeypatch.setattr(module, "MAX_FILES", 2000)
    monkeypatch.setattr(module, "MAX_SEARCH_BYTES", 1)
    with pytest.raises(ValueError, match="byte limit"):
        development.search("absent")


def test_large_edit_input_is_bounded_before_publication(tmp_path):
    (tmp_path / "text.txt").write_bytes(b"task")
    service = AgentDevelopment(
        AgentFiles(Workspace(tmp_path, max_bytes=64), Dictionary({"task": "stage"}))
    )
    read = service.read("text.txt")
    with pytest.raises(ValueError, match="payload exceeds"):
        service.edit(
            "text.txt",
            [{"old_text": "stage", "new_text": "a" * 128}],
            **edit_arguments(read),
        )
    assert (tmp_path / "text.txt").read_bytes() == b"task"
    assert not (tmp_path / HISTORY).exists()


def test_real_mcp_schema_navigation_and_targeted_edit(development):
    async def exercise():
        files = development.files
        server = create_server(
            files.workspace.root, files.dictionary, policy="source.agent-policy.json"
        )
        async with Client(server) as client:
            listing = (await client.call_tool("list_safe_files", {})).data
            assert listing["total"] == 2
            found = (
                await client.call_tool("search_safe_files", {"query": "def stage("})
            ).data
            read = (
                await client.call_tool(
                    "read_safe_file",
                    {
                        "file_path": found["results"][0]["file_path"],
                        "start_line": 2,
                        "end_line": 3,
                    },
                )
            ).data
            result = (
                await client.call_tool(
                    "edit_safe_file",
                    {
                        "file_path": "module.py",
                        "edits": [{"old_text": "x + 1", "new_text": "x * 4"}],
                        **edit_arguments(read),
                    },
                )
            ).data
            assert result["receipt_saved"]
            assert "task" not in json.dumps(result)

    asyncio.run(exercise())
    assert (
        runpy.run_path(str(development.files.workspace.root / "module.py"))["task"](3)
        == 12
    )
