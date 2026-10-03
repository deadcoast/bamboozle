"""Report placement and failure retention across development entry points."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bamboozle import evidence

PROJECT = Path(__file__).resolve().parents[3]
QUALIFIERS = {
    "qualify_pipeline": "VIGILES_PIPELINE_QUALIFICATION.json",
    "qualify_plans": "VIGILES_PLAN_QUALIFICATION.json",
    "qualify_inventory": "VIGILES_INVENTORY_QUALIFICATION.json",
}


def test_default_replacement_archives_exact_failed_bytes(tmp_path):
    project = tmp_path / "project"
    destination = evidence.report_destination(project, "verification.json")
    assert destination == project / evidence.REPORTS / "current/verification.json"
    original = b'{ "status": "failed", "output": "retain me" }\r\n'
    destination.write_bytes(original)
    # Existing default reports are allowed, unlike explicit output paths.
    assert evidence.report_destination(project, destination.name) == destination
    archived = evidence.write_report(
        project, destination, {"status": "passed"}, replace=True
    )
    assert archived is not None
    assert archived.is_relative_to(project / evidence.REPORTS / "archive/runs")
    assert archived.read_bytes() == original
    assert json.loads(destination.read_bytes()) == {"status": "passed"}
    assert list(project.glob("*.json")) == []


def test_explicit_report_is_exclusive(tmp_path):
    project = tmp_path / "project"
    requested = project / "reports/run.json"
    destination = evidence.report_destination(project, "ignored.json", requested)
    assert evidence.write_report(project, destination, {"status": "failed"}) is None
    original = destination.read_bytes()
    with pytest.raises(FileExistsError):
        evidence.report_destination(project, "ignored.json", requested)
    with pytest.raises(FileExistsError):
        evidence.write_report(project, destination, {"status": "passed"})
    assert destination.read_bytes() == original


@pytest.mark.parametrize("failure_at", ["archive", "current"])
def test_publication_failure_preserves_current(tmp_path, monkeypatch, failure_at):
    project = tmp_path / "project"
    destination = evidence.report_destination(project, "verification.json")
    original = b'{"status":"failed"}\n'
    destination.write_bytes(original)
    writer = evidence.atomic_write

    def fail(path, data, **kwargs):
        if (path == destination) == (failure_at == "current"):
            raise OSError("controlled publication failure")
        writer(path, data, **kwargs)

    monkeypatch.setattr(evidence, "atomic_write", fail)
    with pytest.raises(OSError, match="controlled publication failure"):
        evidence.write_report(project, destination, {"status": "passed"}, replace=True)
    assert destination.read_bytes() == original
    archives = list((project / evidence.REPORTS / "archive").rglob("*.json"))
    assert len(archives) == (1 if failure_at == "current" else 0)
    for archive in archives:
        assert archive.read_bytes() == original


@pytest.mark.parametrize("script", ["verify", *QUALIFIERS])
@pytest.mark.parametrize("at_git_root", [False, True])
def test_scripts_reject_root_reports_before_work(tmp_path, script, at_git_root):
    workshop = tmp_path / "must-not-be-created"
    output_root = PROJECT.parent if at_git_root else PROJECT
    requested = output_root / "VIGILES_INVALID_TEST_OUTPUT.json"
    assert not requested.exists()
    args = ["--workshop", str(workshop)] if script in QUALIFIERS else []
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT / "scripts" / f"{script}.py"),
            *args,
            "--report",
            str(requested),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 2, result.stderr
    assert "not the project or repository root" in result.stderr
    assert not requested.exists()
    assert not workshop.exists()


@pytest.mark.parametrize("script,filename", QUALIFIERS.items())
def test_qualifier_default_keeps_failure_when_next_run_passes(
    tmp_path, monkeypatch, script, filename
):
    spec = importlib.util.spec_from_file_location(
        script, PROJECT / "scripts" / f"{script}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    project = tmp_path / "project"
    monkeypatch.setattr(module, "__file__", str(project / "scripts" / f"{script}.py"))
    monkeypatch.setattr(sys, "argv", [script, "--workshop", str(tmp_path / "workshop")])

    def failed(*args):
        raise ValueError("retained qualification failure")

    monkeypatch.setattr(module, "qualify", failed)
    assert module.main() == 1
    destination = project / evidence.REPORTS / "current" / filename
    original = destination.read_bytes()
    assert json.loads(original)["status"] == "failed"

    def passed(root, report=None):
        if report is not None:
            report["status"] = "passed"
        return {"status": "passed"}

    monkeypatch.setattr(module, "qualify", passed)
    assert module.main() == 0
    assert json.loads(destination.read_bytes())["status"] == "passed"
    archived = list((project / evidence.REPORTS / "archive").rglob(filename))
    assert len(archived) == 1 and archived[0].read_bytes() == original
    assert list(project.glob("*.json")) == []
