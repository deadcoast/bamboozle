"""Exercise the original source -> agent edits/generates -> domain source contract."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from bamboozle import service
from bamboozle.cli import main
from bamboozle.dictionary import Dictionary
from bamboozle.mcp import create_server
from bamboozle.translate import main as translate_main
from bamboozle.workspace import Workspace

ORIGINAL = b"\xef\xbb\xbfdef alpha(x):\r\n    return x + 1\r\nassert alpha(3) == 4\r\n# beta is literal\r\n"
GENERATED = b'def beta(x):\n    return x * 2\nassert beta(3) == 6\nassert "__SCM_LIT_beta__" == "bet" + "a"\n'
RESTORED = b'def alpha(x):\n    return x * 2\nassert alpha(3) == 6\nassert "beta" == "bet" + "a"\n'


@pytest.fixture
def project(tmp_path):
    (tmp_path / "node.py").write_bytes(ORIGINAL)
    (tmp_path / "mapping.json").write_text('{"alpha":"beta"}', encoding="utf-8")
    workspace = Workspace(tmp_path)
    service.prepare(workspace, "node.py", dictionary="mapping.json").apply()
    return workspace


def execute(path):
    subprocess.run([sys.executable, str(path)], check=True, timeout=10)


def test_default_decode_restores_agent_edits_with_saved_mapping(project):
    path = project.root / "node_safe.py"
    edited = path.read_bytes().replace(b"x + 1", b"x * 2").replace(b"== 4", b"== 6")
    path.write_bytes(edited)
    # The live dictionary changes; the return path still uses the saved mapping.
    (project.root / "mapping.json").write_text('{"alpha":"gamma"}', encoding="utf-8")
    plan = service.prepare(project, path, mode="reverse")
    assert plan.report()["mapping_source"] == "saved_ledger"
    assert plan.report()["strict"] is False
    plan.apply()
    assert plan.output.read_bytes() == ORIGINAL.replace(b"x + 1", b"x * 2").replace(
        b"== 4", b"== 6"
    )
    execute(plan.output)
    assert path.read_bytes() == edited
    assert (project.root / "node.py").read_bytes() == ORIGINAL
    with pytest.raises(ValueError, match="Encoded file changed"):
        service.verify(project, path)


@pytest.mark.parametrize("context", ["ledger", "dictionary"])
def test_new_agent_file_needs_no_original_counterpart(project, context):
    (project.root / "AGENT_fix.py").write_bytes(GENERATED)
    (project.root / "node.py").unlink()
    plan = service.prepare(
        project,
        "AGENT_fix.py",
        mode="reverse",
        ledger=".node_safe.py.scm_ledger.json" if context == "ledger" else None,
        dictionary="mapping.json" if context == "dictionary" else None,
    )
    assert plan.result == RESTORED
    plan.apply()
    execute(plan.output)
    assert (project.root / "AGENT_fix.py").read_bytes() == GENERATED
    assert not service.ledger_path(
        plan.output
    ).exists()  # Never fabricate original-file integrity.


def test_generated_default_dictionary_return(project):
    mapping = Dictionary.load()
    source, target = next(iter(mapping.items()))
    (project.root / "generated.txt").write_text(target, encoding="utf-8")
    plan = service.prepare(project, "generated.txt", mode="reverse")
    assert plan.result.decode("utf-8") == source
    assert plan.report()["mapping_source"] == "packaged_dictionary"


@pytest.mark.parametrize("strict", [False, True])
def test_explicit_dictionary_must_match_saved_mapping(project, strict):
    with pytest.raises(ValueError, match="does not match"):
        service.prepare(
            project,
            "node_safe.py",
            mode="reverse",
            dictionary=Dictionary({"alpha": "gamma"}),
            strict=strict,
        )


@pytest.mark.parametrize("context", ["ledger", "dictionary", "new_ledger"])
def test_changed_return_context_invalidates_apply(project, context):
    (project.root / "AGENT_fix.py").write_bytes(GENERATED)
    plan = service.prepare(
        project,
        "AGENT_fix.py",
        mode="reverse",
        ledger=".node_safe.py.scm_ledger.json" if context == "ledger" else None,
        dictionary="mapping.json" if context != "ledger" else None,
    )
    changed = project.root / (
        ".node_safe.py.scm_ledger.json" if context == "ledger" else "mapping.json"
    )
    if context == "new_ledger":
        changed = service.ledger_path(plan.source)
        changed.write_bytes(
            (project.root / ".node_safe.py.scm_ledger.json").read_bytes()
        )
    else:
        changed.write_bytes(changed.read_bytes() + b" ")
    with pytest.raises(ValueError, match="after preview"):
        plan.apply()
    assert not plan.output.exists()


@pytest.mark.parametrize("damage", ["invalid_json", "mapping", "digest"])
def test_corrupt_saved_mapping_never_falls_back_to_default(project, damage):
    path = project.root / ".node_safe.py.scm_ledger.json"
    data = json.loads(path.read_bytes())
    if damage == "mapping":
        data["dictionary"]["entries"]["alpha"] = "gamma"
    else:
        data["original_sha256"] = None
    path.write_bytes(b"{" if damage == "invalid_json" else json.dumps(data).encode())
    with pytest.raises(ValueError):
        service.prepare(project, "node_safe.py", mode="reverse")


def test_optional_strict_retains_exact_integrity_contract(project):
    plan = service.prepare(project, "node_safe.py", mode="reverse", strict=True)
    assert plan.result == ORIGINAL
    assert plan.report()["strict"] is True
    (project.root / "AGENT_fix.py").write_bytes(GENERATED)
    with pytest.raises(ValueError, match="requires a saved"):
        service.prepare(project, "AGENT_fix.py", mode="reverse", strict=True)
    with pytest.raises(ValueError, match="Encoded file changed"):
        service.prepare(
            project,
            "AGENT_fix.py",
            mode="reverse",
            ledger=".node_safe.py.scm_ledger.json",
            strict=True,
        )


@pytest.mark.parametrize("option", ["dictionary", "ledger"])
def test_return_context_cannot_escape_workspace(project, option):
    (project.root / "AGENT_fix.py").write_bytes(GENERATED)
    with pytest.raises(ValueError, match="inside the workspace"):
        service.prepare(
            project,
            "AGENT_fix.py",
            mode="reverse",
            dictionary="../outside.json" if option == "dictionary" else None,
            ledger="../outside.json" if option == "ledger" else None,
        )


def test_reverse_expansion_retains_workspace_byte_limit(tmp_path):
    (tmp_path / "generated.txt").write_bytes(b"beta beta")
    with pytest.raises(ValueError, match="size limit"):
        service.prepare(
            Workspace(tmp_path, max_bytes=20),
            "generated.txt",
            mode="reverse",
            dictionary=Dictionary({"a" * 16: "beta"}),
        )


def test_default_decode_preserves_output_and_rejects_damaged_markers(project):
    source = project.root / "AGENT_fix.py"
    source.write_bytes(b"__SCM_LIT_unknown__")
    with pytest.raises(ValueError, match="literal marker"):
        service.prepare(project, source, mode="reverse", dictionary="mapping.json")
    source.write_bytes(GENERATED)
    plan = service.prepare(project, source, mode="reverse", dictionary="mapping.json")
    source.write_bytes(GENERATED + b"# changed\n")
    with pytest.raises(ValueError, match="Input changed"):
        plan.apply()
    plan = service.prepare(project, source, mode="reverse", dictionary="mapping.json")
    plan.output.write_bytes(b"keep this output")
    with pytest.raises(FileExistsError):
        plan.apply()
    assert plan.output.read_bytes() == b"keep this output"


def test_cli_and_compatibility_return_paths_execute_generated_program(project, capsys):
    root = ["--root", str(project.root)]
    (project.root / "AGENT_fix.py").write_bytes(GENERATED)
    args = [
        *root,
        "preview",
        "AGENT_fix.py",
        "--reverse",
        "--ledger",
        ".node_safe.py.scm_ledger.json",
    ]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["substitutions"] == 2 and report["literals"] == 1
    assert not (project.root / report["output"]).exists()
    assert (
        main(
            [
                *root,
                "decode",
                "AGENT_fix.py",
                "--ledger",
                ".node_safe.py.scm_ledger.json",
            ]
        )
        == 0
    )
    execute(project.root / json.loads(capsys.readouterr().out)["output"])
    assert main([*root, "decode", "AGENT_fix.py", "--strict", "-o", "strict.py"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    assert (
        translate_main(
            [
                *root,
                "--mode",
                "reverse",
                "--input",
                "AGENT_fix.py",
                "--output",
                "compat.py",
                "--dict",
                "mapping.json",
            ]
        )
        == 0
    )
    assert (project.root / "compat.py").read_bytes() == RESTORED
    execute(project.root / "compat.py")


def test_mcp_normal_return_uses_saved_or_selected_mapping(project):
    (project.root / "AGENT_fix.py").write_bytes(GENERATED)
    server = create_server(project.root, Dictionary({"different": "vocabulary"}))

    async def exercise():
        async with Client(server) as client:
            for name, options in (
                ("AGENT_fix.py", {"ledger": ".node_safe.py.scm_ledger.json"}),
                (
                    "AGENT_fix.py",
                    {"dictionary": "mapping.json", "output": "explicit.py"},
                ),
                ("node_safe.py", {}),
            ):
                result = await client.call_tool(
                    "preview_file", {"source": name, "reverse": True, **options}
                )
                assert result.content[0].type == "text"
                preview = json.loads(result.content[0].text)
                applied = await client.call_tool(
                    "apply_preview", {"plan_id": preview["plan_id"]}
                )
                assert applied.content[0].type == "text"
                report = json.loads(applied.content[0].text)
                execute(project.root / report["output"])
            rejected = await client.call_tool(
                "preview_file",
                {
                    "source": "AGENT_fix.py",
                    "output": "strict.py",
                    "reverse": True,
                    "strict": True,
                    "ledger": ".node_safe.py.scm_ledger.json",
                },
                raise_on_error=False,
            )
            assert rejected.is_error

    asyncio.run(exercise())


@pytest.mark.skipif(os.name != "nt", reason="Windows agent-return launcher")
def test_drag_drop_return_accepts_newly_generated_filename(tmp_path):
    mapping = Dictionary.load()
    term, target = next(iter(mapping.items()))
    path = tmp_path / "AGENT new output.txt"
    path.write_text(target + "\n", encoding="utf-8")
    source = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["cmd", "/c", str(source / "DECRYPT_FROM_AGENT.bat"), str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=dict(
            os.environ,
            UV_CACHE_DIR=os.environ.get(
                "UV_CACHE_DIR", str(source.parent / ".uv-cache")
            ),
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "AGENT new output_ready.txt").read_text(
        encoding="utf-8"
    ) == term + "\n"
