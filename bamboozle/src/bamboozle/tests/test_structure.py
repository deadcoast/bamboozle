"""Structural rules govern real decoded publications and recoverable batches."""

import copy
import json
import os
import runpy
import subprocess
from pathlib import Path

import pytest

from bamboozle import agent_files, audit, structure, view_import
from bamboozle.agent_files import AgentFiles
from bamboozle.cli import main
from bamboozle.dictionary import Dictionary
from bamboozle.project_views import export_view
from bamboozle.restoration import ReviewedFilePlan, prepare_restore
from bamboozle.service import OperationError, prepare
from bamboozle.tessera import create_key, seal
from bamboozle.utils import json_bytes
from bamboozle.workspace import Workspace


def profile():
    rule = {
        "suffixes": [".py"],
        "formats": {".py": "python"},
        "max_files": 64,
        "max_file_bytes": 65536,
        "max_total_bytes": 1048576,
    }
    return {
        "kind": "bamboozle.structure",
        "schema_version": 1,
        "name": "application",
        "roots": {"app": rule, "tests": copy.deepcopy(rule)},
        "required": {"app/__init__.py": "file", "tests": "directory"},
        "companions": [
            {
                "source": "app",
                "source_suffix": ".py",
                "target": "tests",
                "target_suffix": ".py",
                "target_prefix": "test_",
                "exclude_names": ["__init__.py"],
            }
        ],
        "exclude": [],
    }


def put(root, name, raw):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


@pytest.fixture
def project(tmp_path):
    put(tmp_path, "app/__init__.py", b"")
    put(tmp_path, "app/core.py", b"def alpha(n):\n    return n + 1\n")
    put(
        tmp_path,
        "tests/test_core.py",
        b"from app.core import alpha\nassert alpha(3) == 4\n",
    )
    put(tmp_path, ".vigiles/structure.json", json_bytes(profile()))
    return Workspace(tmp_path)


def test_explicit_profile_and_hash_audit_are_independent(project):
    report = structure.check(project)
    assert report["ok"] and report["files"] == 3 and report["findings_complete"]
    assert audit.audit(project)["status"] == "unbaselined"
    audit.baseline(project)
    assert audit.audit(project)["ok"]
    (project.root / "tests/test_core.py").unlink()
    result = audit.audit(project)
    assert result["status"] == "structure_violations"
    assert result["structure"]["findings"][0]["code"] == "companion"


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "other"),
        ("schema_version", True),
        ("name", "bad name"),
        ("roots", []),
        ("required", []),
        ("companions", {}),
        ("exclude", {}),
    ],
)
def test_schema_rejects_wrong_types(field, value):
    document = profile()
    document[field] = value
    with pytest.raises(ValueError):
        structure.rules(json_bytes(document))


@pytest.mark.parametrize(
    "name",
    [
        "../src",
        "/src",
        "app/../other",
        "app\\part",
        "app/NUL.py",
        "app/aux",
        "app/part.",
        "app/part ",
        "app/.git",
        "app/x:y",
        "app//x",
    ],
)
def test_invalid_rule_paths(name):
    document = profile()
    document["required"] = {name: "file"}
    with pytest.raises(ValueError):
        structure.rules(json_bytes(document))


def test_overlap_and_bad_rule_limits_and_unhashable_values():
    mutations = [
        lambda p: p["roots"].update({"app/child": p["roots"]["app"]}),
        lambda p: p["roots"]["app"].update(max_files=True),
        lambda p: p["roots"]["app"]["formats"].update({".py": []}),
        lambda p: p["companions"][0].update(source=[]),
        lambda p: p["roots"]["app"].update(suffixes=[".PY"]),
        lambda p: p["roots"]["app"].update(suffixes=[".py", ".py"]),
    ]
    for mutate in mutations:
        document = profile()
        mutate(document)
        with pytest.raises(ValueError):
            structure.rules(json_bytes(document))


def test_prospective_batch_creates_companion_and_syntax_is_checked(project):
    additions = {
        "app/nested/util.py": b"def alpha(n):\n    return n * 2\n",
        "tests/nested/test_util.py": b"from app.nested.util import alpha\nassert alpha(4) == 8\n",
    }
    alone = structure.prepare_guard(
        project, {"app/nested/util.py": additions["app/nested/util.py"]}
    )
    assert alone.result is not None and not alone.result["ok"]
    with pytest.raises(structure.StructureViolation):
        alone.recheck()
    assert len(list((project.root / structure.HISTORY).glob("*.json"))) == 1
    guard = structure.prepare_guard(project, additions)
    guard.recheck()
    for name, raw in additions.items():
        put(project.root, name, raw)
        guard.recheck()  # Full overlay remains stable during partial publication.
    assert structure.check(project)["ok"]
    bad = structure.prepare_guard(project, {"app/nested/util.py": b"return 42\n"})
    assert bad.result is not None and bad.result["findings"][0]["code"] == "syntax"


def test_agent_decodes_before_enforcement_and_retains_refused_intent(project):
    files = AgentFiles(project, Dictionary({"alpha": "omega"}))
    read = files.read("app/core.py")
    with pytest.raises(structure.StructureViolation):
        files.write("app/core.py", "def omega(:\n")
    assert project.read("app/core.py") == b"def alpha(n):\n    return n + 1\n"
    denial = json.loads(
        next((project.root / structure.HISTORY).glob("*.json")).read_bytes()
    )
    assert denial["evaluation"]["findings"][0]["code"] == "syntax"
    saved = files.write("app/core.py", read["content"].replace("n + 1", "n * 2"))
    assert saved["structure"]["profile_sha256"] == structure.fingerprint(project)
    assert b"def alpha(n)" in project.read("app/core.py")
    assert b"omega" not in project.read("app/core.py")
    for name in (".vigiles/structure.json", "draft.structure-profile.json"):
        with pytest.raises(ValueError, match="Private"):
            files.read(name)


def test_agent_requires_current_profile_context(project):
    files = AgentFiles(project, Dictionary({"alpha": "omega"}))
    read = files.read("app/core.py")
    document = profile()
    document["name"] = "changed"
    put(project.root, ".vigiles/structure.json", json_bytes(document))
    with pytest.raises(ValueError, match="context changed"):
        files.write("app/core.py", read["content"])
    fresh = files.read("app/core.py")
    assert fresh["context_sha256"] != read["context_sha256"]
    files.write("app/core.py", fresh["content"])


def test_scope_drift_and_profile_activation_invalidate_prepared_publication(project):
    guard = structure.prepare_guard(
        project, {"app/core.py": b"def alpha(n): return n * 2\n"}
    )
    put(project.root, "tests/test_core.py", b"# changed assertion\n")
    with pytest.raises(structure.StructureViolation, match="changed"):
        guard.recheck()
    (project.root / structure.PROFILE).unlink()
    guard = structure.prepare_guard(project, {"new file.txt": b"data"})
    guard.recheck()
    put(project.root, ".vigiles/structure.json", json_bytes(profile()))
    with pytest.raises(structure.StructureViolation, match="changed"):
        guard.recheck()


def test_violations_are_paged_and_pinned_without_mutations(project):
    put(project.root, "app/malformed.py", b"def broken(:")
    put(project.root, "app/stray.txt", b"unexpected")
    first = structure.check(project, limit=1)
    assert not first["ok"] and first["finding_count"] == 3
    second = structure.check(
        project, offset=1, limit=1, expected_sha256=first["check_sha256"]
    )
    assert (
        second["check_sha256"] == first["check_sha256"]
        and second["findings"] != first["findings"]
    )
    assert not (project.root / structure.HISTORY).exists()
    put(project.root, "app/stray.txt", b"changed")
    with pytest.raises(ValueError, match="restart pagination"):
        structure.check(
            project, offset=1, limit=1, expected_sha256=first["check_sha256"]
        )


def test_bounds_exclusions_required_and_format_validation(project, monkeypatch):
    document = profile()
    document["roots"]["app"].update(max_files=1, max_file_bytes=8, max_total_bytes=8)
    put(project.root, ".vigiles/structure.json", json_bytes(document))
    assert {r["code"] for r in structure.check(project)["findings"]} >= {
        "file_count",
        "file_bytes",
        "total_bytes",
    }
    document = profile()
    document["exclude"] = ["app/generated"]
    put(project.root, "app/generated/ignored.dat", b"ignored")
    put(project.root, ".vigiles/structure.json", json_bytes(document))
    assert structure.check(project)["ok"]
    monkeypatch.setattr(structure, "MAX_ENTRIES", 4)
    # Parser caps limits too, so use deliberately small root limits in this fixture.
    for rule in document["roots"].values():
        rule["max_files"] = 4
    put(project.root, ".vigiles/structure.json", json_bytes(document))
    report = structure.check(project)
    assert not report["complete"] and any(
        r["code"] == "scan_limit" for r in report["findings"]
    )


@pytest.mark.parametrize(
    "suffix,format,raw",
    [
        (".json", "json", b'{"a": 1, "a": 2}'),
        (".yaml", "yaml", b"v: [broken"),
        (".txt", "text", b"\xff"),
        (".py", "python", b"return 2"),
    ],
)
def test_format_checks_refuse_invalid_sources(project, suffix, format, raw):
    document = profile()
    document["roots"]["data"] = {
        **document["roots"]["app"],
        "suffixes": [suffix],
        "formats": {suffix: format},
    }
    put(project.root, "data/item" + suffix, raw)
    put(project.root, ".vigiles/structure.json", json_bytes(document))
    report = structure.check(project)
    assert report["findings"] == [
        {
            "code": "syntax",
            "path": "data/item" + suffix,
            "rule": "data",
            "message": f"Correct invalid {format} syntax or UTF-8 encoding",
        }
    ]


def create_view(project):
    put(
        project.root,
        "view-policy.json",
        json_bytes(
            {
                "schema_version": 1,
                "files": {
                    "app/__init__.py": {"policy": "copy"},
                    "app/core.py": {"policy": "translate"},
                    "tests/test_core.py": {"policy": "translate"},
                },
                "new_files": {"app": "translate", "tests": "translate"},
            }
        ),
    )
    result = export_view(
        project,
        "view-policy.json",
        ".local/views/development",
        Dictionary({"alpha": "omega"}),
    )
    return result["view_id"], project.root / result["view"]


def test_reviewed_view_overlay_required_deletion_and_partial_recovery(
    project, monkeypatch
):
    identity, view = create_view(project)
    put(view, "app/new.py", b"def omega(n): return n * 2\n")
    conflict = view_import.review_import(project, identity)
    assert (
        not conflict["ok"]
        and conflict["structural_validation"]["findings"][0]["code"] == "companion"
    )
    put(view, "tests/test_new.py", b"from app.new import omega\nassert omega(4) == 8\n")
    (view / "app/__init__.py").unlink()
    refused = view_import.review_import(
        project, identity, delete_files=["app/__init__.py"]
    )
    assert any(
        row["code"] == "required"
        for row in refused["structural_validation"]["findings"]
    )
    put(view, "app/__init__.py", b"")
    review = view_import.review_import(project, identity)
    assert review["ok"]
    original_write = view_import.atomic_write

    def fail(path, data, **kwargs):
        if path == project.root / "tests/test_new.py":
            raise OSError("injected second-file publication failure")
        return original_write(path, data, **kwargs)

    monkeypatch.setattr(view_import, "atomic_write", fail)
    with pytest.raises(OperationError) as failure:
        view_import.apply_import(project, identity, review["review_sha256"])
    assert (
        failure.value.report["journal_saved"] and failure.value.report["output_saved"]
    )
    assert view_import.view_status(project, identity)["status"] == "pending"
    # Status remains historical even after active operator controls change.
    document = profile()
    document["name"] = "revised"
    put(project.root, ".vigiles/structure.json", json_bytes(document))
    assert view_import.view_status(project, identity)["status"] == "pending"
    with pytest.raises(OperationError, match="changed"):
        view_import.apply_import(
            project, identity, review["review_sha256"], recover=True
        )
    put(project.root, ".vigiles/structure.json", json_bytes(profile()))
    monkeypatch.setattr(view_import, "atomic_write", original_write)
    saved = view_import.apply_import(
        project, identity, review["review_sha256"], recover=True
    )
    assert saved["receipt_saved"] and saved["structure"] == review["structure"]
    assert structure.check(project)["ok"]
    assert b"def alpha(n)" in project.read("app/new.py")


def test_unselected_scoped_drift_invalidates_review(project):
    identity, view = create_view(project)
    put(view, "app/core.py", b"def omega(n): return n * 2\n")
    review = view_import.review_import(project, identity)
    put(project.root, "tests/test_unselected.py", b"assert True\n")
    with pytest.raises(OperationError, match="stale"):
        view_import.apply_import(project, identity, review["review_sha256"])
    assert project.read("app/core.py").endswith(b"return n + 1\n")


def test_conversion_and_reviewed_restoration_apply_the_same_profile(project):
    put(project.root, "draft.txt", b"def alpha(n): return n\n")
    put(project.root, "tests/test_generated.py", b"# companion\n")
    mapping = Dictionary({"alpha": "omega"})
    invalid = prepare(project, "draft.txt", output="app/missing.py", dictionary=mapping)
    assert not invalid.report()["ok"]
    with pytest.raises(OperationError, match="Structural"):
        invalid.apply()
    plan = prepare(project, "draft.txt", output="app/generated.py", dictionary=mapping)
    plan.apply()
    assert structure.check(project)["ok"]
    # Decoding into the declared tree is protected too, including edited returns.
    restored = prepare_restore(
        project,
        "app/generated.py",
        "draft.txt",
        output="app/restored.py",
        terms="translate",
    )
    assert not restored.report()["ok"]
    with pytest.raises(OperationError, match="Structural"):
        restored.apply(restored.review_sha256)
    put(project.root, "tests/test_restored.py", b"# companion\n")
    restored = prepare_restore(
        project,
        "app/generated.py",
        "draft.txt",
        output="app/restored.py",
        terms="translate",
    )
    restored.apply(restored.review_sha256)
    assert project.read("app/restored.py") == project.read("draft.txt")


def test_cli_draft_check_does_not_install_controls(project, capsys):
    (project.root / structure.PROFILE).unlink()
    put(project.root, "draft.structure-profile.json", json_bytes(profile()))
    assert (
        main(
            [
                "--root",
                str(project.root),
                "structure-check",
                "--rules",
                "draft.structure-profile.json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["ok"]
    assert not (project.root / structure.PROFILE).exists()
    (project.root / "tests/test_core.py").unlink()
    assert (
        main(
            [
                "--root",
                str(project.root),
                "structure-check",
                "--rules",
                "draft.structure-profile.json",
            ]
        )
        == 3
    )
    assert not json.loads(capsys.readouterr().out)["ok"]


def test_complete_application_cli_stdio_and_installed_compatible_recovery(tmp_path):
    script = Path(__file__).parents[3] / "scripts/structure_smoke.py"
    result = runpy.run_path(str(script))["run"](tmp_path / "structural-application")
    assert result["status"] == "passed" and result["restored"]["workloads"] == 300
    assert result["recovered"]["receipt_saved"] and result["final_structure"]["ok"]


def test_case_alias_cannot_bypass_scope_and_noncanonical_changes_are_refused(project):
    guard = structure.prepare_guard(project, {"APP/core.py": b"def invalid(:\n"})
    assert guard.result is not None and not guard.result["ok"]
    with pytest.raises(structure.StructureViolation):
        guard.recheck()
    with pytest.raises(ValueError, match="canonical"):
        structure.prepare_guard(project, {"app/../app/core.py": b""})


def test_unavailable_and_changed_malformed_profile_refuse_with_evidence(
    project, monkeypatch
):
    guard = structure.prepare_guard(
        project, {"app/core.py": b"def alpha(n): return n\n"}
    )
    put(project.root, ".vigiles/structure.json", b"invalid control")
    with pytest.raises(structure.StructureViolation):
        guard.recheck()
    assert len(list((project.root / structure.HISTORY).glob("*.json"))) == 1
    with pytest.raises(structure.StructureViolation, match="operator context"):
        structure.fingerprint(project)
    put(project.root, ".vigiles/structure.json", json_bytes(profile()))
    original = Workspace.read

    def unavailable(self, name):
        if str(name).endswith("core.py"):
            raise PermissionError("injected unavailable source")
        return original(self, name)

    monkeypatch.setattr(Workspace, "read", unavailable)
    report = structure.check(project)
    assert not report["ok"] and not report["complete"]
    assert any(row["code"] == "unavailable" for row in report["findings"])


def test_no_baseline_drift_can_still_violate_structure(project):
    (project.root / "tests/test_core.py").unlink()
    audit.baseline(project)
    report = audit.audit(project)
    assert report["changed"] == report["removed"] == report["added"] == []
    assert not report["ok"] and report["structure"]["finding_count"] == 1


def test_decryption_publication_is_independently_guarded(project, capsys):
    key = create_key(project, "exchange.key")
    raw = b"def alpha(n): return n\n"
    put(project.root, "delivery.bin", seal(raw, key.read_bytes(), "receiver"))
    arguments = [
        "--root",
        str(project.root),
        "open",
        "delivery.bin",
        "--key",
        "exchange.key",
        "--recipient",
        "receiver",
        "--output",
        "app/received.py",
    ]
    assert main(arguments) == 1
    failure = json.loads(capsys.readouterr().out)
    assert (
        not failure["output_saved"] and not (project.root / "app/received.py").exists()
    )
    put(
        project.root,
        "tests/test_received.py",
        b"from app.received import alpha\nassert alpha(3) == 3\n",
    )
    assert main(arguments) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["output_saved"] and project.read("app/received.py") == raw


def test_agent_reconnect_with_profile_requires_context_even_without_document_policy(
    project,
):
    files = AgentFiles(project, Dictionary({"alpha": "omega"}))
    read = files.read("app/core.py")
    fresh = AgentFiles(project, files.dictionary)
    with pytest.raises(ValueError, match="context_sha256"):
        fresh.write("app/core.py", read["content"], read["source_sha256"])
    fresh.write(
        "app/core.py", read["content"], read["source_sha256"], read["context_sha256"]
    )


def test_guard_scan_cannot_hide_a_concurrent_target_edit(project, monkeypatch):
    files = AgentFiles(project, Dictionary({"alpha": "omega"}))
    read = files.read("app/core.py")
    prepare_original = agent_files.prepare_guard

    def guarded(workspace, changes):
        guard = prepare_original(workspace, changes)
        recheck = guard.recheck
        calls = 0

        def changed(*, record=True):
            nonlocal calls
            recheck(record=record)
            calls += 1
            if calls == 2:
                put(project.root, "app/core.py", b"# concurrent operator update\n")

        monkeypatch.setattr(guard, "recheck", changed)
        return guard

    monkeypatch.setattr(agent_files, "prepare_guard", guarded)
    with pytest.raises(OperationError, match="during structural validation") as failure:
        files.write("app/core.py", read["content"])
    assert not failure.value.report["output_saved"]
    assert project.read("app/core.py") == b"# concurrent operator update\n"


def test_reviewed_metadata_is_part_of_the_proposed_tree(project):
    # Generic reviewed publication also serves classical transforms and dictionaries.
    put(project.root, "tests/test_new.py", b"# test companion\n")
    plan = ReviewedFilePlan(
        project,
        project.root / "app/new.py",
        project.root / "app/unsupported.receipt",
        {},
        b"VALUE = 1\n",
        b"{}",
        {},
    )
    assert not plan.report()["ok"]
    with pytest.raises(OperationError, match="Structural"):
        plan.apply(plan.review_sha256)
    assert not (project.root / "app/new.py").exists()


def test_findings_truncation_is_explicit_and_never_passes(project, monkeypatch):
    monkeypatch.setattr(structure, "MAX_FINDINGS", 1)
    put(project.root, "app/malformed.py", b"def broken(:\n")
    result = structure.check(project)
    assert not result["ok"] and result["findings_truncated"]
    assert result["finding_count"] == 2 and len(result["findings"]) == 1
    assert not result["findings_complete"] and result["next_offset"] is None


def test_private_state_and_outside_scope_do_not_block_other_operations(project):
    (project.root / "tests/test_core.py").unlink()
    guard = structure.prepare_guard(
        project, {"outside.txt": b"Allowed outside the roots"}
    )
    guard.recheck()
    assert guard.result is None
    document = profile()
    document["roots"] = {
        ".": {
            **document["roots"]["app"],
            "suffixes": [".py", ".json"],
            "formats": {".py": "python", ".json": "json"},
        }
    }
    document["companions"] = []
    put(project.root, ".vigiles/structure.json", json_bytes(document))
    put(project.root, ".local/view/private.txt", b"not public")
    assert structure.check(project)["ok"]


def test_root_file_and_overlay_cannot_replace_a_declared_directory(project):
    document = profile()
    document["roots"]["new.py"] = document["roots"]["app"]
    put(project.root, ".vigiles/structure.json", json_bytes(document))
    guard = structure.prepare_guard(project, {"new.py": b"VALUE = 1\n"})
    assert guard.result is not None and any(
        row["code"] == "root_kind" for row in guard.result["findings"]
    )


def test_removed_profile_invalidates_pagination(project):
    prior = structure.check(project)
    (project.root / structure.PROFILE).unlink()
    with pytest.raises(ValueError, match="restart pagination"):
        structure.check(project, expected_sha256=prior["check_sha256"])


def test_profile_changed_during_evaluation_is_detected_and_retained(
    project, monkeypatch
):
    guard = structure.prepare_guard(
        project, {"app/core.py": b"def alpha(n): return n\n"}
    )
    evaluate = structure._evaluate

    def concurrent_change(*args):
        result = evaluate(*args)
        put(
            project.root,
            ".vigiles/structure.json",
            json_bytes({**profile(), "name": "changed"}),
        )
        return result

    monkeypatch.setattr(structure, "_evaluate", concurrent_change)
    with pytest.raises(structure.StructureViolation, match="changed"):
        guard.recheck()
    assert len(list((project.root / structure.HISTORY).glob("*.json"))) == 1
    assert project.read("app/core.py").endswith(b"return n + 1\n")


def test_root_under_a_link_is_never_followed(project):
    link = project.root / "linked"
    try:
        link.symlink_to(project.root / "app", target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(project.root / "app")],
            check=True,
            capture_output=True,
        )
    document = profile()
    document["roots"]["linked/nested"] = document["roots"]["app"]
    put(project.root, ".vigiles/structure.json", json_bytes(document))
    checked = structure.check(project)
    assert not checked["ok"] and not checked["complete"]
    assert checked["findings"][0]["code"] == "unavailable"


def test_profile_json_nesting_and_duplicate_keys_fail_with_bounded_diagnostics():
    for raw in (b'[{"private": 1, "private": 2}]', b"[" * 10000 + b"]" * 10000):
        with pytest.raises(ValueError, match="strict UTF-8 JSON"):
            structure.rules(raw)
