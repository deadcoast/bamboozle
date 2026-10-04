import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from fastmcp import Client

from bamboozle.cli import main
from bamboozle.mcp import create_server
from bamboozle.migration import prepare_migration
from bamboozle.restoration import prepare_restore
from bamboozle.service import prepare, verify
from bamboozle.utils import json_bytes
from bamboozle.workspace import Workspace

REPOSITORY = Path(__file__).resolve().parents[4]


def historical_module():
    spec = importlib.util.spec_from_file_location(
        "historical_cipher", REPOSITORY / "example_project/dev_tools/cipher.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def old(tmp_path):
    # Use the retained historical implementation, not the migration implementation,
    # to create real interoperable v2 evidence with its underscore boundary rules.
    legacy = historical_module()
    original = tmp_path / "source.py"
    encoded = tmp_path / "source_safe.py"
    raw = b"\xef\xbb\xbfalpha_value = 4\r\nbeta_value = 8\r\n# alpha beta\r\n"
    original.write_bytes(raw)
    mapping = {"alpha": "beta"}
    text = legacy.process_forward(raw.decode("utf-8"), mapping)
    encoded.write_bytes(text.encode("utf-8"))
    ledger = tmp_path / ".source_safe.py.scm_ledger.json"
    ledger.write_bytes(
        json_bytes(
            legacy.make_ledger(raw.decode("utf-8"), text, mapping, original, encoded)
        )
    )
    return Workspace(tmp_path), original, encoded, ledger


@pytest.mark.parametrize("version", [1, 2])
def test_real_legacy_pair_migrates_and_strictly_restores(old, version):
    workspace, original, encoded, ledger = old
    if version == 1:
        document = json.loads(ledger.read_bytes())
        document["version"] = 1
        for name in ("dictionary_sha256", "original_sha256", "encoded_sha256"):
            document.pop(name)
        ledger.write_bytes(json_bytes(document))
    before = {p: p.read_bytes() for p in (original, encoded, ledger)}
    plan = prepare_migration(workspace, encoded, original, ledger=ledger)
    assert plan.report()["historical_profile"] == f"standalone-v{version}"
    assert plan.report()["encoding_changed"]
    assert plan.result is not None and b"alpha_value" in plan.result
    assert b"beta_value" in encoded.read_bytes()
    assert not plan.output.exists()
    # CLI reconstructs the same reviewed metadata in a later invocation.
    assert (
        prepare_migration(workspace, encoded, original, ledger=ledger).review_sha256
        == plan.review_sha256
    )
    plan.apply(plan.review_sha256)
    assert verify(workspace, plan.output)["status"] == "verified"
    restored = prepare(workspace, plan.output, mode="reverse")
    restored.apply()
    assert restored.output.read_bytes() == original.read_bytes()
    assert all(p.read_bytes() == data for p, data in before.items())
    # The migrated output is now usable by the actual native edit workflow.
    plan.output.write_bytes(plan.output.read_bytes().replace(b"= 4", b"= 9"))
    edited = prepare_restore(workspace, plan.output, original, output="edited.py")
    edited.apply(edited.review_sha256)
    assert edited.output.read_bytes() == original.read_bytes().replace(b"= 4", b"= 9")


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_global_ledger_migration_retains_original_bytes(tmp_path, newline):
    original = tmp_path / "source.txt"
    encoded = tmp_path / "source_safe.txt"
    ledger = tmp_path / ".scm_ledger.json"
    original.write_bytes(b"alpha and beta" + newline)
    encoded.write_bytes(b"beta and __SCM_LIT_beta__\n")
    ledger.write_bytes(
        json_bytes(
            {
                "original_file": original.name,
                "track_literals": ["beta"],
                "timestamp": "2024-01-01T00:00:00Z",
            }
        )
    )
    (tmp_path / "mapping.json").write_bytes(b'{"alpha":"beta"}')
    workspace = Workspace(tmp_path)
    with pytest.raises(ValueError, match="explicit --dict"):
        prepare_migration(workspace, encoded, original, ledger=ledger)
    plan = prepare_migration(
        workspace, encoded, original, ledger=ledger, dictionary="mapping.json"
    )
    assert plan.report()["historical_newlines_normalized"] == (newline != b"\n")
    plan.apply(plan.review_sha256)
    restored = prepare(workspace, plan.output, mode="reverse")
    assert restored.result == original.read_bytes()
    assert ledger.exists() and encoded.read_bytes() == b"beta and __SCM_LIT_beta__\n"


@pytest.mark.parametrize(
    "field",
    [
        "dictionary_sha256",
        "original_sha256",
        "encoded_sha256",
        "encoded_file",
        "original_file",
        "version",
    ],
)
def test_corrupt_historical_evidence_refuses_migration(old, field):
    workspace, original, encoded, ledger = old
    document = json.loads(ledger.read_bytes())
    document[field] = "altered"
    ledger.write_bytes(json_bytes(document))
    before = {p.name: p.read_bytes() for p in workspace.root.iterdir()}
    with pytest.raises(ValueError):
        prepare_migration(workspace, encoded, original, ledger=ledger)
    assert {p.name: p.read_bytes() for p in workspace.root.iterdir()} == before


def test_edited_legacy_pair_and_mismatched_dictionary_are_rejected(old):
    workspace, original, encoded, ledger = old
    (workspace.root / "wrong.json").write_bytes(b'{"alpha":"gamma"}')
    with pytest.raises(ValueError, match="differs"):
        prepare_migration(
            workspace, encoded, original, ledger=ledger, dictionary="wrong.json"
        )
    encoded.write_bytes(encoded.read_bytes() + b"# agent edit")
    with pytest.raises(ValueError, match="digests"):
        prepare_migration(workspace, encoded, original, ledger=ledger)


def test_global_ledger_unknown_fields_and_literal_conflict_refused(tmp_path):
    (tmp_path / "source.txt").write_bytes(b"alpha beta")
    (tmp_path / "encoded.txt").write_bytes(b"beta __SCM_LIT_beta__")
    (tmp_path / "map.json").write_bytes(b'{"alpha":"beta"}')
    record = {
        "original_file": "source.txt",
        "track_literals": [],
        "timestamp": "historical",
    }
    ledger = tmp_path / "old.json"
    ledger.write_bytes(json_bytes(record))
    with pytest.raises(ValueError, match="literal evidence"):
        prepare_migration(
            Workspace(tmp_path),
            "encoded.txt",
            "source.txt",
            ledger="old.json",
            dictionary="map.json",
        )
    record["unrecognized"] = True
    ledger.write_bytes(json_bytes(record))
    with pytest.raises(ValueError, match="Unsupported"):
        prepare_migration(
            Workspace(tmp_path),
            "encoded.txt",
            "source.txt",
            ledger="old.json",
            dictionary="map.json",
        )


def test_cli_and_mcp_migration_share_review_and_application(old, capsys):
    workspace, original, encoded, ledger = old
    args = [
        "--root",
        str(workspace.root),
        "migrate-ledger",
        encoded.name,
        "--original",
        original.name,
        "--ledger",
        ledger.name,
        "-o",
        "cli.txt",
    ]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert main([*args, "--apply", "--review-sha256", report["review_sha256"]]) == 0
    capsys.readouterr()

    async def exercise():
        async with Client(create_server(workspace.root)) as client:
            response = await client.call_tool(
                "preview_migration",
                {
                    "source": encoded.name,
                    "original": original.name,
                    "ledger": ledger.name,
                    "output": "mcp.txt",
                },
            )
            content = response.content[0]
            assert content.type == "text"
            report = json.loads(content.text)
            await client.call_tool("apply_preview", {"plan_id": report["plan_id"]})
            assert (workspace.root / "mcp.txt").read_bytes() == (
                workspace.root / "cli.txt"
            ).read_bytes()
            assert verify(workspace, "mcp.txt")["status"] == "verified"

    asyncio.run(exercise())
