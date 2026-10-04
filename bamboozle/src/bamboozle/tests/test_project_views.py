"""Complete neutral project development and reviewed return, including failures."""

import asyncio
import json
import os
import subprocess
import sys

import pytest
from fastmcp import Client

from bamboozle import project_views, view_import
from bamboozle.cli import main
from bamboozle.dictionary import Dictionary
from bamboozle.mcp import create_server
from bamboozle.project_views import CONTRACT, export_view, load_view, read_policy
from bamboozle.service import OperationError
from bamboozle.utils import (
    PublicationError,
    atomic_write,
    exclusive_lock,
    json_bytes,
    sha256,
)
from bamboozle.view_import import apply_import, review_import, view_status
from bamboozle.workspace import Workspace

ORIGINAL = (
    b"\xef\xbb\xbfdef fUcK(value):\r\n    return value + 1\r\nassert fUcK(3) == 4\r\n"
)
EDITED = ORIGINAL.replace(b"value + 1", b"value * 2").replace(b"== 4", b"== 6")
TOKEN = "__SCM_CASE_a_kinetic_impulse__"


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/node.py").write_bytes(ORIGINAL)
    (tmp_path / "src/obsolete.py").write_bytes(b"OLD = True\n")
    (tmp_path / "settings.json").write_bytes(b'{"enabled":false}\r\n')
    (tmp_path / "production.json").write_bytes(b'{"prompt":"private domain content"}\n')
    (tmp_path / "neutral.json").write_bytes(b'{"prompt":"neutral fixture","id":17}\n')
    (tmp_path / "private.txt").write_bytes(b"MUST NOT BE EXPORTED")
    (tmp_path / ".env").write_bytes(b"PRIVATE_SECRET=retained")
    policy = {
        "schema_version": 1,
        "files": {
            "src/node.py": {"policy": "translate"},
            "src/obsolete.py": {"policy": "copy"},
            "settings.json": {"policy": "copy"},
            "production.json": {"policy": "fixture", "fixture": "neutral.json"},
            "private.txt": {"policy": "exclude"},
        },
        "new_files": {"src": "translate", "tests": "copy"},
    }
    (tmp_path / "view-policy.json").write_bytes(json_bytes(policy))
    workspace = Workspace(tmp_path)
    exported = export_view(
        workspace,
        "view-policy.json",
        ".local/views/task",
        Dictionary.load(profile="profanity"),
    )
    return workspace, exported, tmp_path / exported["view"]


def edit_project(view):
    node = view / "src/node.py"
    node.write_bytes(
        node.read_bytes().replace(b"value + 1", b"value * 2").replace(b"== 4", b"== 6")
    )
    (view / "src/generated.py").write_bytes(
        f"from node import {TOKEN}\nassert {TOKEN}(5) == 10\n".encode()
    )
    (view / "settings.json").write_bytes(b'{"enabled":true}\r\n')
    (view / "production.json").write_bytes(b'{"prompt":"agent changed fixture"}\n')


def execute(workspace):
    subprocess.run(
        [sys.executable, str(workspace.root / "src/generated.py")],
        check=True,
        timeout=10,
    )


def test_export_is_scoped_usable_and_keeps_reverse_data_outside_view(project):
    workspace, exported, view = project
    assert sorted(
        path.relative_to(view).as_posix() for path in view.rglob("*") if path.is_file()
    ) == [
        CONTRACT,
        "production.json",
        "settings.json",
        "src/node.py",
        "src/obsolete.py",
    ]
    assert (view / "settings.json").read_bytes() == (
        workspace.root / "settings.json"
    ).read_bytes()
    assert (view / "production.json").read_bytes() == (
        workspace.root / "neutral.json"
    ).read_bytes()
    assert TOKEN.encode() in (view / "src/node.py").read_bytes()
    assert b"fuck" not in (view / "src/node.py").read_bytes().lower()
    subprocess.run([sys.executable, str(view / "src/node.py")], check=True, timeout=10)
    directory, data, _, _, _ = load_view(workspace, exported["view_id"])
    assert not directory.is_relative_to(view)
    assert (
        project_views.blob(
            workspace, directory, data["files"]["src/node.py"]["original_sha256"]
        )
        == ORIGINAL
    )
    assert view_status(workspace, exported["view_id"])["status"] == "ready"


def test_multi_file_edit_generation_fixture_and_explicit_deletion_roundtrip(project):
    workspace, exported, view = project
    edit_project(view)
    (view / "src/obsolete.py").unlink()
    refused = review_import(workspace, exported["view_id"])
    assert not refused["ok"] and refused["review_sha256"] is None
    review = review_import(
        workspace, exported["view_id"], delete_files=["src/obsolete.py"]
    )
    assert review["ok"] and review["action_count"] == 4
    assert {change["action"] for change in review["changes"]} == {
        "create",
        "modify",
        "delete",
        "fixture_ignored",
    }
    assert (workspace.root / "src/node.py").read_bytes() == ORIGINAL
    saved = apply_import(
        workspace,
        exported["view_id"],
        review["review_sha256"],
        delete_files=["src/obsolete.py"],
    )
    assert saved["status"] == "applied" and saved["receipt_saved"]
    assert (workspace.root / "src/node.py").read_bytes() == EDITED
    assert (workspace.root / "settings.json").read_bytes() == b'{"enabled":true}\r\n'
    assert (
        workspace.root / "production.json"
    ).read_bytes() == b'{"prompt":"private domain content"}\n'
    assert not (workspace.root / "src/obsolete.py").exists()
    execute(workspace)
    assert view_status(workspace, exported["view_id"])["status"] == "complete"
    with pytest.raises(ValueError, match="completed"):
        apply_import(
            workspace, exported["view_id"], review["review_sha256"], recover=True
        )
    # Completion remains historical after legitimate subsequent source work.
    (workspace.root / "src/node.py").write_bytes(EDITED + b"# later work\n")
    status = view_status(workspace, exported["view_id"])
    assert (
        status["status"] == "complete" and status["files"]["src/node.py"] == "conflict"
    )


@pytest.mark.parametrize("changed", ["source", "view", "new_destination", "contract"])
def test_stale_review_refuses_all_source_writes(project, changed):
    workspace, exported, view = project
    edit_project(view)
    review = review_import(workspace, exported["view_id"])
    target = {
        "source": workspace.root / "src/node.py",
        "view": view / "src/node.py",
        "new_destination": workspace.root / "src/generated.py",
        "contract": view / CONTRACT,
    }[changed]
    target.write_bytes(b"concurrent change")
    before = (workspace.root / "settings.json").read_bytes()
    with pytest.raises((OperationError, ValueError)):
        apply_import(workspace, exported["view_id"], review["review_sha256"])
    assert (workspace.root / "settings.json").read_bytes() == before
    assert not (workspace.root / exported["private_state"] / "import.json").exists()


@pytest.mark.parametrize(
    "name",
    [
        "outside.py",
        "private.txt",
        "view-policy.json",
        "src/.env",
        "src/key.key",
        "src/cipher_dict.json",
        "src/profanity_dict.json",
        "src/.local/private.py",
    ],
)
def test_unapproved_and_private_new_files_are_conflicts(project, name):
    workspace, exported, view = project
    target = view / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"print('must not import')\n")
    report = review_import(workspace, exported["view_id"])
    assert not report["ok"] and report["review_sha256"] is None
    assert any(row["path"] == name for row in report["conflicts"])


def test_saved_mapping_and_policy_are_independent_of_later_configuration(project):
    workspace, exported, view = project
    (workspace.root / "view-policy.json").write_bytes(b"edited config")
    edit_project(view)
    review = review_import(workspace, exported["view_id"])
    apply_import(workspace, exported["view_id"], review["review_sha256"])
    execute(workspace)


def test_saved_custom_mapping_and_literal_targets_survive_later_edits(tmp_path):
    workspace = Workspace(tmp_path)
    mapping = Dictionary({"alpha": "omega"})
    (tmp_path / "mapping.json").write_bytes(json_bytes(mapping.document()))
    (tmp_path / "node.txt").write_bytes(b"alpha omega\r\n")
    (tmp_path / "policy.json").write_bytes(
        json_bytes(
            {
                "schema_version": 1,
                "files": {"node.txt": {"policy": "translate"}},
                "new_files": {".": "translate"},
            }
        )
    )
    exported = export_view(
        workspace,
        "policy.json",
        "view",
        mapping,
        private_paths=(tmp_path / "mapping.json",),
    )
    view = tmp_path / "view"
    assert (view / "node.txt").read_bytes() == b"omega __SCM_LIT_omega__\r\n"
    assert "alpha" not in (view / CONTRACT).read_text(encoding="utf-8")
    (tmp_path / "mapping.json").write_bytes(b"changed dictionary")
    (view / "node.txt").write_bytes(b"omega __SCM_LIT_omega__ omega\r\n")
    (view / "new.txt").write_bytes(b"omega __SCM_LIT_omega__\n")
    reviewed = review_import(workspace, exported["view_id"])
    apply_import(workspace, exported["view_id"], reviewed["review_sha256"])
    assert (tmp_path / "node.txt").read_bytes() == b"alpha omega alpha\r\n"
    assert (tmp_path / "new.txt").read_bytes() == b"alpha omega\n"


def test_review_is_read_only_and_no_changes_does_not_consume_view(project):
    workspace, exported, view = project
    before = {
        path: path.read_bytes() for path in workspace.root.rglob("*") if path.is_file()
    }
    review = review_import(workspace, exported["view_id"])
    assert review["action_count"] == 0
    assert before == {
        path: path.read_bytes() for path in workspace.root.rglob("*") if path.is_file()
    }
    assert (
        apply_import(workspace, exported["view_id"], review["review_sha256"])["status"]
        == "no_changes"
    )
    edit_project(view)
    assert review_import(workspace, exported["view_id"])["action_count"] == 3


@pytest.mark.parametrize(
    "failure",
    ["import.json", "settings.json", "generated.py", "node.py", "result.json"],
)
def test_interrupted_import_retains_bytes_and_can_complete_explicitly(
    project, monkeypatch, failure
):
    workspace, exported, view = project
    edit_project(view)
    review = review_import(workspace, exported["view_id"])

    def fail(path, data, *, overwrite=False):
        if path.name == failure:
            raise OSError("injected publication failure")
        atomic_write(path, data, overwrite=overwrite)

    monkeypatch.setattr(view_import, "atomic_write", fail)
    with pytest.raises(OperationError) as caught:
        apply_import(workspace, exported["view_id"], review["review_sha256"])
    assert caught.value.report["journal_saved"] == (failure != "import.json")
    assert caught.value.report["output_saved"] == (
        failure not in {"import.json", "settings.json"}
    )
    directory, data, _, _, _ = load_view(workspace, exported["view_id"])
    assert (
        project_views.blob(
            workspace, directory, data["files"]["src/node.py"]["original_sha256"]
        )
        == ORIGINAL
    )
    monkeypatch.setattr(view_import, "atomic_write", atomic_write)
    saved = apply_import(
        workspace,
        exported["view_id"],
        review["review_sha256"],
        recover=failure != "import.json",
    )
    assert saved["receipt_saved"]
    execute(workspace)


@pytest.mark.parametrize("failure", ["import.json", "generated.py", "result.json"])
def test_post_publication_cleanup_failure_reports_actual_state(
    project, monkeypatch, failure
):
    workspace, exported, view = project
    edit_project(view)
    review = review_import(workspace, exported["view_id"])

    def fail(path, data, *, overwrite=False):
        atomic_write(path, data, overwrite=overwrite)
        if path.name == failure:
            raise PublicationError("injected cleanup failure")

    monkeypatch.setattr(view_import, "atomic_write", fail)
    with pytest.raises(OperationError) as caught:
        apply_import(workspace, exported["view_id"], review["review_sha256"])
    assert caught.value.report["journal_saved"]
    assert caught.value.report["receipt_saved"] == (failure == "result.json")
    assert caught.value.report["output_saved"] == (failure != "import.json")
    monkeypatch.setattr(view_import, "atomic_write", atomic_write)
    if failure != "result.json":
        apply_import(
            workspace, exported["view_id"], review["review_sha256"], recover=True
        )
    assert view_status(workspace, exported["view_id"])["status"] == "complete"


@pytest.mark.parametrize(
    "conflict", ["source", "view", "snapshot", "journal", "digest"]
)
def test_recovery_rejects_conflicting_or_corrupt_state(project, monkeypatch, conflict):
    workspace, exported, view = project
    edit_project(view)
    review = review_import(workspace, exported["view_id"])

    def fail(path, data, *, overwrite=False):
        if path.name == "generated.py":
            raise OSError("injected failure")
        atomic_write(path, data, overwrite=overwrite)

    monkeypatch.setattr(view_import, "atomic_write", fail)
    with pytest.raises(OperationError):
        apply_import(workspace, exported["view_id"], review["review_sha256"])
    monkeypatch.setattr(view_import, "atomic_write", atomic_write)
    directory, manifest, _, _, _ = load_view(workspace, exported["view_id"])
    if conflict == "source":
        (workspace.root / "src/node.py").write_bytes(b"external change")
    elif conflict == "view":
        (view / "src/node.py").write_bytes(b"different agent edit")
    elif conflict == "snapshot":
        (
            directory
            / "blobs"
            / f"{manifest['files']['src/node.py']['original_sha256']}.bin"
        ).write_bytes(b"damaged")
    elif conflict == "journal":
        document = json.loads((directory / "import.json").read_bytes())
        document["plan"]["actions"][0]["path"] = "private.txt"
        document["review_sha256"] = sha256(json_bytes(document["plan"]))
        (directory / "import.json").write_bytes(json_bytes(document))
    token = "0" * 64 if conflict == "digest" else review["review_sha256"]
    with pytest.raises((OperationError, ValueError)):
        apply_import(workspace, exported["view_id"], token, recover=True)
    assert not (workspace.root / "src/generated.py").exists()


def test_source_change_during_publication_withholds_receipt(project, monkeypatch):
    workspace, exported, view = project
    edit_project(view)
    review = review_import(workspace, exported["view_id"])

    def race(path, data, *, overwrite=False):
        atomic_write(path, data, overwrite=overwrite)
        if path.name == "node.py":
            (workspace.root / "settings.json").write_bytes(b"external change")

    monkeypatch.setattr(view_import, "atomic_write", race)
    with pytest.raises(OperationError, match="changed during") as caught:
        apply_import(workspace, exported["view_id"], review["review_sha256"])
    assert (
        caught.value.report["output_saved"] and not caught.value.report["receipt_saved"]
    )
    assert (workspace.root / "settings.json").read_bytes() == b"external change"


def test_multiple_view_writers_share_one_lock(project):
    workspace, exported, view = project
    edit_project(view)
    review = review_import(workspace, exported["view_id"])
    with (
        exclusive_lock(project_views.operation_lock(workspace)),
        pytest.raises(OSError, match="Another Bamboozle"),
    ):
        apply_import(workspace, exported["view_id"], review["review_sha256"])


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "a/../b",
        "a//b",
        "a\\b",
        "/absolute",
        "nul.py",
        "a:stream",
        "a.",
        ".env",
        "cipher_dict.json",
    ],
)
def test_unsafe_export_paths_and_private_files_are_refused(tmp_path, name):
    (tmp_path / "policy.json").write_bytes(
        json_bytes(
            {"schema_version": 1, "files": {name: {"policy": "copy"}}, "new_files": {}}
        )
    )
    with pytest.raises((OSError, ValueError)):
        export_view(Workspace(tmp_path), "policy.json", "view")
    assert not (tmp_path / "view").exists()


def test_policy_rejects_ambiguous_and_unknown_rules():
    for files in (
        {"a": {"policy": "guess"}},
        {"a": {"policy": []}},
        {"a": {"policy": "copy", "extra": True}},
        {"a": {"policy": "fixture"}},
        {"a": {"policy": "copy"}, "A": {"policy": "translate"}},
        {"a": {"policy": "copy"}, "a/b": {"policy": "copy"}},
    ):
        with pytest.raises(ValueError):
            read_policy(
                json_bytes({"schema_version": 1, "files": files, "new_files": {}})
            )


def test_export_never_replaces_existing_view_and_requires_selected_files(project):
    workspace, exported, view = project
    before = (view / "src/node.py").read_bytes()
    with pytest.raises(FileExistsError):
        export_view(workspace, "view-policy.json", exported["view"])
    assert (view / "src/node.py").read_bytes() == before


def test_export_failure_preserves_partial_view_and_original(project, monkeypatch):
    workspace, _, _ = project

    def fail(path, data, *, overwrite=False):
        if path.name == "manifest.json":
            raise OSError("injected manifest failure")
        atomic_write(path, data, overwrite=overwrite)

    monkeypatch.setattr(project_views, "atomic_write", fail)
    with pytest.raises(OperationError) as caught:
        export_view(workspace, "view-policy.json", "second-view")
    assert not caught.value.report["manifest_saved"]
    assert caught.value.report["files_exported"] == 5
    assert (workspace.root / "second-view/src/node.py").exists()
    assert (workspace.root / "src/node.py").read_bytes() == ORIGINAL


def test_limits_and_damaged_case_markers_refuse_import(project, monkeypatch):
    workspace, exported, view = project
    (view / "src/node.py").write_bytes(b"__SCM_CASE_broken__")
    assert not review_import(workspace, exported["view_id"])["ok"]
    monkeypatch.setattr(project_views, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(ValueError, match="byte limit"):
        export_view(workspace, "view-policy.json", "too-large")


def test_unknown_deletion_and_foreign_manifest_are_rejected(project):
    workspace, exported, _ = project
    with pytest.raises(ValueError, match="Deletion selection"):
        review_import(workspace, exported["view_id"], delete_files=["src/node.py"])
    path = workspace.root / exported["private_state"] / "manifest.json"
    data = json.loads(path.read_bytes())
    data["source_root"] += "-other"
    path.write_bytes(json_bytes(data))
    with pytest.raises(ValueError, match="foreign"):
        view_status(workspace, exported["view_id"])


def test_link_in_view_is_not_followed(project):
    workspace, exported, view = project
    target = workspace.root / "private.txt"
    try:
        (view / "src/linked.txt").symlink_to(target)
    except OSError:
        if os.name != "nt":
            raise
        # Windows directory junctions are available without symlink privileges.
        subprocess.run(
            [
                "cmd",
                "/c",
                "mklink",
                "/J",
                str(view / "linked"),
                str(workspace.root / "src"),
            ],
            check=True,
            capture_output=True,
        )
    with pytest.raises(ValueError, match="link|junction"):
        review_import(workspace, exported["view_id"])


def test_cli_review_apply_and_status_use_the_same_pipeline(project, capsys):
    workspace, exported, view = project
    edit_project(view)
    args = ["--root", str(workspace.root)]
    assert main([*args, "view-review", exported["view_id"]]) == 0
    reviewed = json.loads(capsys.readouterr().out)
    assert (
        main(
            [
                *args,
                "view-apply",
                exported["view_id"],
                "--review-sha256",
                reviewed["review_sha256"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert main([*args, "view-status", exported["view_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    execute(workspace)


def test_operator_mcp_export_review_apply_and_status(project):
    workspace, _, _ = project
    server = create_server(workspace.root, Dictionary.load(profile="profanity"))

    async def exercise():
        async with Client(server) as client:

            async def call(name, args):
                result = await client.call_tool(name, args)
                content = result.content[0]
                assert content.type == "text"
                return json.loads(content.text)

            exported = await call(
                "export_project_view",
                {"config": "view-policy.json", "output": "mcp-view"},
            )
            edit_project(workspace.root / exported["view"])
            review = await call("review_project_view", {"view_id": exported["view_id"]})
            saved = await call(
                "apply_project_view",
                {
                    "view_id": exported["view_id"],
                    "review_sha256": review["review_sha256"],
                },
            )
            assert saved["status"] == "applied"
            assert (
                await call("project_view_status", {"view_id": exported["view_id"]})
            )["status"] == "complete"

    asyncio.run(exercise())
    execute(workspace)
