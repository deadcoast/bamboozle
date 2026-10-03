import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bamboozle.cli import main
from workshop.mcp import workshop_mcp
from workshop.pipeline import inspect_pipeline, run_script
from workshop.scrape import bamb5


@pytest.fixture
def pipeline(tmp_path):
    scrape = tmp_path / "workshop with spaces" / "scrape"
    scrape.mkdir(parents=True)
    fixture = Path(__file__).with_name("fixtures") / "pipeline_contract.py"
    for name in ("promote.py", "audit_matrix.py"):
        (scrape / name).write_bytes(fixture.read_bytes())
    (scrape / "_lora_manifest.json").write_text(
        json.dumps({"loras": {"A": {"tags": ["red hair"]}, "B": {}}}), encoding="utf-8"
    )
    (scrape / "_compatibility_matrix.json").write_text(
        '{"pairs": {"A|B": {"score": 0.8}}}', encoding="utf-8"
    )
    (scrape / "original.bin").write_bytes(b"synthetic fixture\0\r\n")
    return scrape


def snapshot(root):
    return {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}


def test_readiness_does_not_execute_or_mutate_inputs(pipeline):
    (pipeline / "promote.py").write_text(
        'raise RuntimeError("inspection must not execute me")', encoding="utf-8"
    )
    before = snapshot(pipeline)
    report = inspect_pipeline(pipeline, Path(sys.executable))
    assert report["ok"] and report["status"] == "ready_for_review"
    assert not report["deployment_qualified"] and not report["executed_scripts"]
    assert all(check["ok"] for check in report["checks"])
    assert "red hair" not in json.dumps(report)
    assert snapshot(pipeline) == before


def test_readiness_missing_workspace_does_not_create_it(tmp_path, capsys):
    missing = tmp_path / "absent"
    code = main(["workshop-check", "--workshop", str(missing)])
    report = json.loads(capsys.readouterr().out)
    assert code == 3 and report["status"] == "needs_attention"
    assert not missing.exists()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("promote.py", "def broken("),
        ("_lora_manifest.json", '{"loras": {"A": {"tags": "not a list"}}}'),
        ("_lora_manifest.json", '{"loras": {}, "loras": {}}'),
        ("_lora_manifest.json", '{"loras": []}'),
        ("_compatibility_matrix.json", '{"pairs": {"A|missing": {}}}'),
        ("_compatibility_matrix.json", '{"pairs": {"A|A": {}}}'),
        ("_compatibility_matrix.json", '{"pairs": {"B|A": {}}}'),
        ("_compatibility_matrix.json", '{"pairs": {"A|B": {"score": NaN}}}'),
    ],
)
def test_invalid_dependencies_prevent_readiness(pipeline, name, content):
    (pipeline / name).write_text(content, encoding="utf-8")
    report = inspect_pipeline(pipeline, Path(sys.executable))
    assert not report["ok"]
    assert next(check for check in report["checks"] if check["name"] == name)["error"]


@pytest.mark.parametrize("module", [workshop_mcp, bamb5])
def test_adapter_arguments_are_identical_and_dry_run_is_default(
    pipeline, monkeypatch, module
):
    monkeypatch.setattr(module, "SCRAPE", pipeline)
    monkeypatch.setattr(module, "PY", Path(sys.executable))
    before = snapshot(pipeline)
    assert module.pipeline_readiness()["ok"]
    candidate = 'candidate with spaces & "quotes"'
    operations = [
        (module.promote_lora(candidate), ["--candidate", candidate, "--dry-run"]),
        (module.promote_lora(candidate, dry_run=False), ["--candidate", candidate]),
        (
            module.quarantine_lora("A", "explicit fixture reason"),
            ["--quarantine", "A", "--reason", "explicit fixture reason"],
        ),
        (module.run_audit(), []),
        (module.run_audit(prune=True), ["--prune"]),
    ]
    for report, expected in operations:
        assert report["returncode"] == 0 and report["status"] == "completed"
        output = json.loads(report["stdout"])
        assert output == {"args": expected, "cwd": str(pipeline)}
        assert report["side_effects_possible"] and not report["deployment_qualified"]
        assert len(report["script_sha256"]) == 64
    assert snapshot(pipeline) == before


def test_failed_publication_is_preserved_for_explicit_fixture_recovery(pipeline):
    original = (pipeline / "original.bin").read_bytes()
    report = run_script(
        pipeline, Path(sys.executable), "promote.py", "--candidate", "fail-after-copy"
    )
    assert report["status"] == "failed" and report["returncode"] == 9
    assert report["side_effects_possible"] and not report["retry_performed"]
    assert not report["deployment_qualified"]
    assert (pipeline / "partial.bin").read_bytes() == original
    assert not (pipeline / "recovery.json").exists()
    recovery = run_script(
        pipeline, Path(sys.executable), "promote.py", "--candidate", "recover-copy"
    )
    assert recovery["status"] == "completed"
    assert json.loads((pipeline / "recovery.json").read_text())["recovered"]
    assert (pipeline / "partial.bin").read_bytes() == original
    assert (pipeline / "original.bin").read_bytes() == original


def test_timeout_retains_partial_output_and_returns_structured_failure(pipeline):
    report = run_script(
        pipeline,
        Path(sys.executable),
        "promote.py",
        "--candidate",
        "timeout-after-copy",
        timeout=2,
    )
    assert report["status"] == "timed_out" and report["returncode"] == 124
    assert report["started"] and report["side_effects_possible"]
    assert "ledger not saved" in report["stdout"]
    assert report["termination_scope"] == "direct_child_only"
    assert (pipeline / "partial.bin").exists()
    assert not (pipeline / "recovery.json").exists()


def test_non_utf8_child_output_is_reported_and_returned_tails_are_limited(pipeline):
    report = run_script(
        pipeline, Path(sys.executable), "promote.py", "--candidate", "output"
    )
    assert report["status"] == "completed"
    assert len(report["stdout"]) == 8000 and len(report["stderr"]) == 4000
    assert report["stdout_truncated"] and report["stderr_truncated"]
    assert report["stdout"].endswith("\ufffd")


def test_spawn_failure_is_not_reported_as_execution(pipeline, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("fixture denied execution")

    monkeypatch.setattr(subprocess, "Popen", fail)
    report = run_script(pipeline, Path(sys.executable), "promote.py")
    assert report["status"] == "start_failed" and report["returncode"] == 126
    assert not report["started"] and not report["side_effects_possible"]


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 901])
def test_invalid_timeout_does_not_start_a_process(pipeline, timeout):
    with pytest.raises(ValueError, match="timeout"):
        run_script(pipeline, Path(sys.executable), "promote.py", timeout=timeout)


def test_missing_or_invalid_script_does_not_execute(pipeline):
    (pipeline / "promote.py").write_text("invalid syntax :", encoding="utf-8")
    report = run_script(pipeline, Path(sys.executable), "promote.py")
    assert report["status"] == "unavailable" and not report["started"]
    with pytest.raises(ValueError, match="Unsupported"):
        run_script(pipeline, Path(sys.executable), "../promote.py")


def test_configured_interpreter_keeps_its_environment(pipeline):
    (pipeline / "promote.py").write_text(
        "import json, sys\nfrom bamboozle import __version__\n"
        "print(json.dumps({'prefix': sys.prefix, 'version': __version__}))\n",
        encoding="utf-8",
    )
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json\nfrom pathlib import Path\nfrom workshop import config\n"
                "from workshop.pipeline import run_script\n"
                "print(json.dumps(run_script(Path.cwd(), config.PYTHON, 'promote.py')))"
            ),
        ],
        cwd=pipeline,
        env=dict(os.environ, VIGILES_WORKSHOP_PYTHON=sys.executable),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    report = json.loads(probe.stdout)
    assert report["status"] == "completed", report
    assert (
        Path(json.loads(report["stdout"])["prefix"]).resolve()
        == Path(sys.prefix).resolve()
    )
