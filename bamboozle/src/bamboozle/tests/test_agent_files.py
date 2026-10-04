"""The original SCM agent contract: encoded reads and decoded writes to source."""

import asyncio
import json
import subprocess
import sys

import pytest
from fastmcp import Client

from bamboozle import agent_files
from bamboozle.agent_files import AgentFiles
from bamboozle.cipher import CipherEngine, process_forward, process_reverse
from bamboozle.dictionary import Dictionary
from bamboozle.mcp import create_server as general_server
from bamboozle.scm_mcp import create_server
from bamboozle.service import OperationError
from bamboozle.utils import PublicationError, atomic_write, sha256
from bamboozle.workspace import Workspace

ORIGINAL = b"\xef\xbb\xbfdef alpha(x):\r\n    return x + 1\r\nassert alpha(3) == 4\r\n# beta is literal\r\n"
MAPPING = Dictionary({"alpha": "beta"})


@pytest.fixture
def files(tmp_path):
    (tmp_path / "node.py").write_bytes(ORIGINAL)
    return AgentFiles(Workspace(tmp_path), MAPPING)


def test_cipher_api_is_the_encoder_used_by_agent(files):
    encoded = CipherEngine(MAPPING).encode(ORIGINAL.decode("utf-8"))
    assert "def beta(x)" in encoded and "alpha" not in encoded
    assert "__SCM_LIT_beta__" in encoded
    assert CipherEngine(MAPPING).decode(encoded).encode("utf-8") == ORIGINAL
    assert process_forward(ORIGINAL.decode("utf-8"), MAPPING) == encoded
    assert process_reverse(encoded, MAPPING).encode("utf-8") == ORIGINAL
    assert files.read("node.py")["content"] == encoded


def test_agent_reads_encoded_source_then_edits_authoritative_file(files):
    root = files.workspace.root
    read = files.read("node.py")
    assert "alpha" not in json.dumps(read)
    assert sorted(p.name for p in root.iterdir()) == ["node.py"]
    response = read["content"].replace("x + 1", "x * 2").replace("== 4", "== 6")
    result = files.write("node.py", response)
    assert result["status"] == "saved" and result["backup_saved"]
    assert result["output_saved"] and result["receipt_saved"]
    assert "alpha" not in json.dumps(result) and "content" not in result
    expected = ORIGINAL.replace(b"x + 1", b"x * 2").replace(b"== 4", b"== 6")
    assert (root / "node.py").read_bytes() == expected
    assert (root / result["history"] / "original.bin").read_bytes() == ORIGINAL
    assert (root / result["history"] / "decoded.bin").read_bytes() == expected
    assert json.loads((root / result["history"] / "result.json").read_bytes()) == result
    subprocess.run([sys.executable, str(root / "node.py")], check=True, timeout=10)
    assert "return x * 2" in files.read("node.py")["content"]


def test_agent_generates_new_module_without_original(files):
    report = files.write(
        "new.py", "def beta(x):\n    return x * 3\nassert beta(4) == 12\n"
    )
    assert report["before_sha256"] is None and not report["backup_saved"]
    assert (
        files.workspace.root / "new.py"
    ).read_bytes() == b"def alpha(x):\n    return x * 3\nassert alpha(4) == 12\n"
    subprocess.run(
        [sys.executable, str(files.workspace.root / "new.py")], check=True, timeout=10
    )


def test_read_hash_allows_reconnect_and_wrong_hash_refuses_write(files):
    read = files.read("node.py")
    restarted = AgentFiles(files.workspace, MAPPING)
    with pytest.raises(ValueError, match="Read the existing"):
        restarted.write("node.py", read["content"])
    with pytest.raises(ValueError, match="Source changed"):
        restarted.write("node.py", read["content"], "0" * 64)
    assert (
        restarted.write("node.py", read["content"], read["source_sha256"])["status"]
        == "saved"
    )


def test_concurrent_edit_and_deleted_source_are_not_overwritten(files):
    read = files.read("node.py")
    source = files.workspace.root / "node.py"
    source.write_bytes(b"someone else's work")
    with pytest.raises(ValueError, match="Source changed"):
        files.write("node.py", read["content"])
    assert source.read_bytes() == b"someone else's work"
    source.unlink()
    with pytest.raises(ValueError, match="source is missing"):
        files.write("node.py", read["content"])
    assert not source.exists()


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        ".git/config",
        ".vigiles/cipher/changes/x/original.bin",
        ".mivim/spec.md",
        ".local/dict.json",
        "cipher_dict.json",
        "secret.key",
        "secret.key.abc.bak",
        ".node.scm_ledger.json",
    ],
)
def test_private_paths_and_escapes_are_not_exposed_or_written(files, path):
    with pytest.raises(ValueError):
        files.read(path)
    with pytest.raises(ValueError):
        files.write(path, "beta")


def test_custom_dictionary_path_is_private(tmp_path):
    path = tmp_path / "private-mapping.json"
    MAPPING.save(path)
    files = AgentFiles(Workspace(tmp_path), MAPPING, private_paths=(path,))
    with pytest.raises(ValueError, match="Private cipher"):
        files.read(path.name)
    with pytest.raises(ValueError, match="Private cipher"):
        files.write(path.name, "{}")


def test_damaged_markers_and_metadata_destinations_refuse_publication(files):
    with pytest.raises(ValueError, match="literal marker"):
        files.write("new.py", "__SCM_LIT_unknown__")
    (files.workspace.root / ".new.py.scm_ledger.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="translation metadata"):
        files.write("new.py", "beta")
    assert not (files.workspace.root / "new.py").exists()


@pytest.mark.parametrize(
    "failure_file",
    ["original.bin", "decoded.bin", "intent.json", "node.py", "result.json"],
)
def test_partial_write_reports_actual_state_and_retains_original(
    files, monkeypatch, failure_file
):
    read = files.read("node.py")

    def fail(path, data, *, overwrite=False):
        if path.name == failure_file:
            raise PermissionError("injected publication failure")
        atomic_write(path, data, overwrite=overwrite)

    monkeypatch.setattr(agent_files, "atomic_write", fail)
    with pytest.raises(OperationError) as caught:
        files.write("node.py", read["content"].replace("x + 1", "x * 2"))
    report = caught.value.report
    assert report["output_saved"] == (failure_file == "result.json")
    if failure_file != "original.bin":
        assert report["backup_saved"]
        assert (
            files.workspace.root / report["history"] / "original.bin"
        ).read_bytes() == ORIGINAL
    if not report["output_saved"]:
        assert (files.workspace.root / "node.py").read_bytes() == ORIGINAL
    assert not report["receipt_saved"]


def test_source_change_during_backup_is_retained_and_refused(files, monkeypatch):
    read = files.read("node.py")
    source = files.workspace.root / "node.py"

    def race(path, data, *, overwrite=False):
        atomic_write(path, data, overwrite=overwrite)
        if path.name == "intent.json":
            source.write_bytes(b"concurrent change")

    monkeypatch.setattr(agent_files, "atomic_write", race)
    with pytest.raises(OperationError, match="changed before publication"):
        files.write("node.py", read["content"])
    assert source.read_bytes() == b"concurrent change"


@pytest.mark.parametrize("failure_file", ["original.bin", "node.py", "result.json"])
def test_post_publication_cleanup_failure_reports_published_files(
    files, monkeypatch, failure_file
):
    read = files.read("node.py")

    def fail_after_publish(path, data, *, overwrite=False):
        atomic_write(path, data, overwrite=overwrite)
        if path.name == failure_file:
            raise PublicationError("injected cleanup failure")

    monkeypatch.setattr(agent_files, "atomic_write", fail_after_publish)
    with pytest.raises(OperationError) as caught:
        files.write("node.py", read["content"])
    report = caught.value.report
    assert report["backup_saved"]
    assert report["output_saved"] == (failure_file != "original.bin")
    assert report["receipt_saved"] == (failure_file == "result.json")
    assert (
        files.workspace.root / report["history"] / "original.bin"
    ).read_bytes() == ORIGINAL


def test_bundled_cipher_translates_metadata_for_agent_and_restores_new_tags(tmp_path):
    mapping = Dictionary.load()
    terms = list(mapping.items())
    source, target = terms[0]
    added_source, added_target = terms[1]
    original = {"triggers": [source], "native": target, "score": 0.5}
    (tmp_path / "metadata.json").write_text(json.dumps(original), encoding="utf-8")
    files = AgentFiles(Workspace(tmp_path), mapping)
    neutral = json.loads(files.read("metadata.json")["content"])
    assert neutral["triggers"] == [target]
    neutral["triggers"].append(added_target)
    neutral["score"] = 0.75
    files.write("metadata.json", json.dumps(neutral))
    restored = json.loads((tmp_path / "metadata.json").read_bytes())
    assert restored == {
        "triggers": [source, added_source],
        "native": target,
        "score": 0.75,
    }


def test_agent_file_limit_and_read_context_bound(tmp_path, monkeypatch):
    files = AgentFiles(Workspace(tmp_path, max_bytes=32), MAPPING)
    with pytest.raises(ValueError, match="file limit"):
        files.write("new.py", "b" * 33)
    monkeypatch.setattr(agent_files, "MAX_READS", 1)
    for name in ("one", "two"):
        (tmp_path / name).write_bytes(b"alpha")
        files.read(name)
    with pytest.raises(ValueError, match="Read the existing"):
        files.write("one", "beta")
    assert files.write("one", "beta", sha256(b"alpha"))["status"] == "saved"


@pytest.mark.parametrize("factory", [create_server, general_server])
def test_agent_mcp_cipher_read_edit_write_and_generate(files, factory):
    server = factory(files.workspace.root, MAPPING)

    async def call(client, name, args):
        result = await client.call_tool(name, args)
        content = result.content[0]
        assert content.type == "text"
        return json.loads(content.text)

    async def exercise():
        async with Client(server) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert {"read_safe_file", "write_safe_file"} <= names
            if factory is create_server:
                assert names == {"read_safe_file", "write_safe_file"}
            read = await call(client, "read_safe_file", {"file_path": "node.py"})
            assert "alpha" not in json.dumps(read)
            updated = read["content"].replace("x + 1", "x * 2").replace("== 4", "== 6")
            saved = await call(
                client, "write_safe_file", {"file_path": "node.py", "content": updated}
            )
            assert saved["output_saved"] and "alpha" not in json.dumps(saved)
            generated = await call(
                client,
                "write_safe_file",
                {
                    "file_path": "new.py",
                    "content": "from node import beta\nassert beta(4) == 8\n",
                },
            )
            assert generated["output_saved"]
            await asyncio.to_thread(
                subprocess.run,
                [sys.executable, str(files.workspace.root / "new.py")],
                check=True,
                timeout=10,
            )

    asyncio.run(exercise())
