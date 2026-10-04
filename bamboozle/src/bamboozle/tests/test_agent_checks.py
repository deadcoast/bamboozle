"""Real neutral check execution, repair cycles, retained evidence and process lifetime."""

import asyncio
import json
import runpy
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from bamboozle.agent_checks import CHECK_TOOLS, HISTORY, AgentChecks
from bamboozle.agent_development import AGENT_TOOLS, AgentDevelopment
from bamboozle.agent_files import AgentFiles
from bamboozle.agent_policy import AgentPolicy
from bamboozle.dictionary import Dictionary
from bamboozle.python_project import validate_project
from bamboozle.scm_mcp import create_server
from bamboozle.utils import json_bytes
from bamboozle.workspace import Workspace


@pytest.fixture
def checks(tmp_path):
    rules = {"format": "python", "identifiers": ["task"], "modules": ["module"]}
    (tmp_path / "module.py").write_bytes(
        b'\xef\xbb\xbfdef task(x):\r\n    return x + 1\r\nLABEL = "task"\r\n'
    )
    (tmp_path / "check_app.py").write_bytes(
        b'from module import task\nassert task(2) == 4, "task result must equal 4"\nprint("stage succeeded")\n'
    )
    (tmp_path / "private.txt").write_bytes(b"private source")
    (tmp_path / "source.agent-policy.json").write_bytes(
        json_bytes(
            {
                "schema_version": 2,
                "files": {
                    name: {"policy": "translate", "document": rules}
                    for name in ("module.py", "check_app.py")
                },
                "new_files": {"checks": {"policy": "translate", "document": rules}},
            }
        )
    )
    (tmp_path / "source.agent-checks.json").write_bytes(
        json_bytes(
            {
                "schema_version": 1,
                "checks": {
                    "application": {
                        "python": ["check_app.py"],
                        "directory": ".",
                        "timeout_seconds": 10,
                        "output_bytes": 4096,
                    }
                },
            }
        )
    )
    workspace = Workspace(tmp_path)
    files = AgentFiles(
        workspace,
        Dictionary({"task": "stage"}),
        policy=AgentPolicy(workspace, "source.agent-policy.json"),
    )
    return AgentChecks(files, "source.agent-checks.json")


def run_check(checks):
    listed = checks.catalog()["checks"][0]
    return checks.run(listed["check_id"], listed["snapshot_sha256"])


def fix_source(checks):
    io = AgentDevelopment(checks.files)
    read = io.read("module.py")
    return io.edit(
        "module.py",
        [{"old_text": "x + 1", "new_text": "x * 2"}],
        read["source_sha256"],
        read["context_sha256"],
        read["neutral_sha256"],
    )


def change_command(checks, arguments, **limits):
    config = json.loads(checks.path.read_bytes())
    config["checks"]["application"].update(python=arguments, **limits)
    checks.path.write_bytes(json_bytes(config))
    return AgentChecks(checks.files, checks.path.name)


def test_failing_check_corrected_through_cipher_then_passes_after_reconnect(checks):
    before = checks.files.workspace.read("module.py")
    reads = dict(checks.files.reads)
    failed = run_check(checks)
    assert failed["status"] == "failed", failed
    assert failed["returncode"] == 1 and failed["source_current"]
    assert failed["cleanup"]["complete"] and failed["capture_complete"]
    assert "assert stage(2)" in failed["stderr"] and "task" not in failed["stderr"]
    assert dict(checks.files.reads) == reads
    directory = checks.files.workspace.root / HISTORY / failed["run_id"]
    assert (
        (directory / "workspace/module.py")
        .read_bytes()
        .startswith(b"\xef\xbb\xbfdef stage")
    )
    assert not (directory / "workspace/private.txt").exists()
    assert not (directory / "workspace/source.agent-policy.json").exists()
    assert checks.files.workspace.read("module.py") == before
    assert checks.inspect(failed["run_id"])["matches_current_source"]
    fix_source(checks)
    assert not checks.inspect(failed["run_id"])["matches_current_source"]
    restarted = AgentChecks(checks.files, checks.path.name)
    passed = run_check(restarted)
    assert passed["status"] == "passed", passed
    assert passed["stdout"].splitlines() == ["stage succeeded"]
    assert passed["run_id"] != failed["run_id"]
    assert (directory / "result.json").is_file()
    subprocess.run(
        [sys.executable, "check_app.py"],
        cwd=checks.files.workspace.root,
        check=True,
        capture_output=True,
    )


def test_stale_snapshot_and_check_configuration_refuse_execution(checks):
    listed = checks.catalog()["checks"][0]
    fix_source(checks)
    with pytest.raises(ValueError, match="snapshot changed"):
        checks.run("application", listed["snapshot_sha256"])
    assert not list((checks.files.workspace.root / HISTORY).glob("*/intent.json"))
    change_command(checks, ["module.py"])
    with pytest.raises(ValueError, match="configuration changed"):
        checks.catalog()


def test_generated_tests_enter_a_fresh_snapshot_and_old_digest_is_refused(checks):
    listed = checks.catalog()["checks"][0]
    checks.files.write(
        "checks/generated.py", "from module import stage\nassert stage(2) == 3\n"
    )
    with pytest.raises(ValueError, match="snapshot changed"):
        checks.run("application", listed["snapshot_sha256"])
    checks = change_command(checks, ["-m", "checks.generated"])
    assert run_check(checks)["status"] == "passed"


@pytest.mark.parametrize(
    "command",
    [
        ["-c", "print(1)"],
        ["../outside.py"],
        ["-m"],
        ["-m", "bad;module"],
        ["check_app.py", "line\nbreak"],
        ["program.exe"],
    ],
)
def test_operator_command_schema_is_explicit_and_shell_free(checks, command):
    with pytest.raises(ValueError):
        change_command(checks, command)


def test_checks_need_explicit_policy_and_private_configuration(checks):
    with pytest.raises(ValueError, match="explicit --policy"):
        AgentChecks(
            AgentFiles(checks.files.workspace, checks.files.dictionary),
            checks.path.name,
        )
    with pytest.raises(ValueError, match="private"):
        AgentChecks(checks.files, "checks.json")
    with pytest.raises(ValueError, match="Private"):
        checks.files.read(checks.path.name)
    with pytest.raises(ValueError, match="Private"):
        checks.files.write(checks.path.name, "{}")


def test_noisy_streams_are_bounded_without_inheriting_operator_secrets(
    checks, monkeypatch
):
    root = checks.files.workspace.root
    monkeypatch.setenv("BAMBOOZLE_PRIVATE_TEST_SECRET", "secret-value")
    checks.files.write(
        "checks/noisy.py",
        'import os, sys\nassert "BAMBOOZLE_PRIVATE_TEST_SECRET" not in os.environ\nfor i in range(1000):\n    print("stage " * 20)\n    print("stage " * 20, file=sys.stderr)\n',
    )
    checks = change_command(checks, ["checks/noisy.py"], output_bytes=512)
    result = run_check(checks)
    assert result["status"] == "passed", result
    for name in ("stdout", "stderr"):
        assert result["capture"][name]["truncated"]
        assert result["capture"][name]["bytes_retained"] <= 512
        assert len(result[name].encode()) <= 512
    assert (root / HISTORY / result["run_id"] / "result.json").is_file()


@pytest.mark.parametrize("parent_exits", [False, True])
def test_timeout_and_normal_exit_clean_up_spawned_children(checks, parent_exits):
    code = 'import subprocess, sys, time\nfrom pathlib import Path\nsubprocess.Popen([sys.executable, "-c", "import time; from pathlib import Path; time.sleep(3); Path(\\"escaped.txt\\").write_text(\\"alive\\")"])\n'
    if not parent_exits:
        code += "time.sleep(20)\n"
    checks.files.write("checks/child.py", code)
    checks = change_command(checks, ["checks/child.py"], timeout_seconds=1)
    begin = time.monotonic()
    result = run_check(checks)
    assert result["status"] == (
        "capture_incomplete" if parent_exits else "timed_out"
    ), result
    assert result["cleanup"]["complete"]
    assert time.monotonic() - begin < 5
    directory = checks.files.workspace.root / HISTORY / result["run_id"] / "workspace"
    time.sleep(3.1)
    assert not (directory / "escaped.txt").exists()


def test_check_cannot_count_as_pass_after_rewriting_an_input(checks):
    checks.files.write(
        "checks/rewrite.py",
        'from pathlib import Path\nPath("module.py").write_text("value = 1\\n")\n',
    )
    checks = change_command(checks, ["checks/rewrite.py"])
    original = checks.files.workspace.read("module.py")
    result = run_check(checks)
    assert result["status"] == "workspace_changed"
    assert result["modified_inputs"] == ["module.py"]
    assert checks.files.workspace.read("module.py") == original


def test_retained_receipt_tampering_and_incomplete_records_are_distinct(checks):
    failed = run_check(checks)
    directory = checks.files.workspace.root / HISTORY / failed["run_id"]
    (directory / "result.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="digest differs"):
        checks.inspect(failed["run_id"])
    (directory / "result.sha256").unlink()
    assert checks.inspect(failed["run_id"])["status"] == "incomplete"


def test_source_drift_during_execution_cannot_be_reported_as_a_current_pass(
    checks, monkeypatch
):
    import bamboozle.agent_checks as module

    fix_source(checks)
    actual_runner = module.run_python

    def change_after_execution(*args):
        result = actual_runner(*args)
        assert result["status"] == "passed"
        checks.files.workspace.resolve("module.py").write_bytes(
            b"def task(x):\n    return x * 7\n"
        )
        return result

    monkeypatch.setattr(module, "run_python", change_after_execution)
    result = run_check(checks)
    assert (
        result["status"] == "source_changed" and result["execution_status"] == "passed"
    )
    assert not result["source_current"]


def test_catalog_values_cannot_mutate_configured_commands(checks):
    catalog = checks.catalog()
    catalog["checks"][0]["python"][0] = "unselected.py"
    assert checks.catalog()["checks"][0]["python"] == ["check_app.py"]


def test_diagnostic_translation_is_single_pass_preserves_neutral_names_and_bounds_utf8(
    checks,
):
    directory = checks.files.workspace.root / HISTORY / "synthetic"
    text, shortened = checks._display("task stage task", directory, 4096)
    assert text == "stage stage stage" and not shortened
    text, shortened = checks._display("\U0001f600" * 100, directory, 257)
    assert shortened and len(text.encode("utf-8")) <= 257


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job assignment contract")
def test_windows_containment_failure_does_not_start_project_code(checks, monkeypatch):
    import bamboozle.check_process as module

    checks.files.write(
        "checks/start.py",
        'from pathlib import Path\nPath("started.txt").write_text("started")\n',
    )
    checks = change_command(checks, ["checks/start.py"])

    def refuse(self, pid):
        raise OSError("deliberate job assignment failure")

    monkeypatch.setattr(module._Job, "assign", refuse)
    result = run_check(checks)
    assert result["status"] == "runner_error" and not result["started"]
    assert not (
        checks.files.workspace.root
        / HISTORY
        / result["run_id"]
        / "workspace/started.txt"
    ).exists()


def test_src_layout_imports_execute_the_selected_neutral_copy(checks):
    root = checks.files.workspace.root
    policy = json.loads(checks.files.policy.path.read_bytes())
    policy["new_files"]["src"] = policy["new_files"]["checks"]
    checks.files.policy.path.write_bytes(json_bytes(policy))
    files = AgentFiles(
        checks.files.workspace,
        checks.files.dictionary,
        policy=AgentPolicy(checks.files.workspace, "source.agent-policy.json"),
    )
    files.write("src/sample/__init__.py", "def stage(x):\n    return x * 2\n")
    # This check's project import is explicitly selected, so both the call and
    # provider translate while its filesystem-origin assertion stays literal.
    policy["new_files"]["checks"]["document"]["modules"] = ["module", "sample"]
    checks.files.policy.path.write_bytes(json_bytes(policy))
    files = AgentFiles(
        checks.files.workspace,
        checks.files.dictionary,
        policy=AgentPolicy(checks.files.workspace, "source.agent-policy.json"),
    )
    files.write(
        "checks/layout.py",
        'import sample\nfrom pathlib import Path\nassert Path(sample.__file__).is_relative_to(Path.cwd())\nassert sample.stage(3) == 6\nprint("stage in copied src")\n',
    )
    checks = change_command(
        AgentChecks(files, checks.path.name),
        ["checks/layout.py"],
        python_paths=["src", "."],
    )
    result = run_check(checks)
    assert result["status"] == "passed", result
    assert result["stdout"].strip() == "stage in copied src"
    assert b"def task" in (root / "src/sample/__init__.py").read_bytes()


def test_project_runtime_bindings_are_limited_to_actual_file_and_package_attributes():
    files = {
        "module.py": b"value = 1\n",
        "package/__init__.py": b"",
        "main.py": b"from module import __file__\nimport module\nimport package\nassert module.__spec__\nassert package.__path__\n",
    }
    assert (
        validate_project(files, {"main.py": ("module", "package")})["status"]
        == "checked"
    )
    for attribute in ("__path__", "__made_up__"):
        files["main.py"] = f"import module\nvalue = module.{attribute}\n".encode()
        with pytest.raises(ValueError, match="not bound"):
            validate_project(files, {"main.py": ("module",)})


def test_standalone_stdio_check_entrypoint_executes_and_retains_results(checks):
    dictionary_path = checks.files.workspace.root / "mapping.dictionary.json"
    dictionary_path.write_bytes(json_bytes(checks.files.dictionary.document()))

    async def exercise():
        transport = StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "bamboozle.scm_mcp",
                "--root",
                str(checks.files.workspace.root),
                "--dict",
                dictionary_path.name,
                "--policy",
                "source.agent-policy.json",
                "--checks",
                checks.path.name,
            ],
        )
        async with Client(transport) as client:
            catalog = (await client.call_tool("list_safe_checks", {})).data
            result = (
                await client.call_tool(
                    "run_safe_check",
                    {
                        "check_id": "application",
                        "expected_snapshot": catalog["checks"][0]["snapshot_sha256"],
                    },
                )
            ).data
            assert result["status"] == "failed" and result["cleanup"]["complete"]
            return result["run_id"]

    run_id = asyncio.run(exercise())
    assert checks.inspect(run_id)["matches_current_source"]


def test_real_mcp_check_failure_edit_rerun_and_inspection(checks):
    async def exercise():
        server = create_server(
            checks.files.workspace.root,
            checks.files.dictionary,
            policy="source.agent-policy.json",
            checks=checks.path.name,
        )
        async with Client(server) as client:
            assert {
                tool.name for tool in await client.list_tools()
            } == AGENT_TOOLS | CHECK_TOOLS
            catalog = (await client.call_tool("list_safe_checks", {})).data
            failed = (
                await client.call_tool(
                    "run_safe_check",
                    {
                        "check_id": "application",
                        "expected_snapshot": catalog["checks"][0]["snapshot_sha256"],
                    },
                )
            ).data
            assert failed["status"] == "failed"
            read = (
                await client.call_tool("read_safe_file", {"file_path": "module.py"})
            ).data
            await client.call_tool(
                "edit_safe_file",
                {
                    "file_path": "module.py",
                    "edits": [{"old_text": "x + 1", "new_text": "x * 2"}],
                    "expected_sha256": read["source_sha256"],
                    "expected_context_sha256": read["context_sha256"],
                    "expected_neutral_sha256": read["neutral_sha256"],
                },
            )
            catalog = (await client.call_tool("list_safe_checks", {})).data
            passed = (
                await client.call_tool(
                    "run_safe_check",
                    {
                        "check_id": "application",
                        "expected_snapshot": catalog["checks"][0]["snapshot_sha256"],
                    },
                )
            ).data
            assert passed["status"] == "passed", passed
            inspected = (
                await client.call_tool(
                    "inspect_safe_check", {"run_id": failed["run_id"]}
                )
            ).data
            assert (
                inspected["status"] == "failed"
                and not inspected["matches_current_source"]
            )

    asyncio.run(exercise())


def test_vandal_routes_check_configuration_without_running_code(checks, monkeypatch):
    root = Path(__file__).parents[3]
    function = runpy.run_path(str(root / "scripts/vandal.py"))["context"]
    for name in (
        "pyproject.toml",
        ".mivim/vigiles-vandal_0.2.md",
        ".vigiles/skills/vigiles-vandal/SKILL.md",
    ):
        path = checks.files.workspace.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((root / name).read_bytes())
    monkeypatch.setitem(function.__globals__, "ROOT", checks.files.workspace.root)
    result = function(
        "agent", policy="source.agent-policy.json", checks=checks.path.name
    )
    assert result["application_checks"]["check_ids"] == ["application"]
    assert result["mcp_config"]["mcpServers"]["bamboozle_agent"]["args"][-2:] == [
        "--checks",
        str(checks.path),
    ]
    assert not (checks.files.workspace.root / HISTORY).exists()
