import asyncio
import json
import subprocess
import sys

import pytest
from fastmcp import Client

from bamboozle import restoration, service
from bamboozle.cli import main
from bamboozle.dictionary import Dictionary
from bamboozle.mcp import create_server
from bamboozle.utils import atomic_write, json_bytes, sha256
from bamboozle.workspace import Workspace


@pytest.fixture
def edit(tmp_path):
    original = b"\xef\xbb\xbfdef alpha(x):\r\n    return x + 1\r\n\r\nassert alpha(2) == 3\r\n# beta stays literal\r\n"
    (tmp_path / "original.py").write_bytes(original)
    workspace = Workspace(tmp_path)
    service.prepare(
        workspace, "original.py", dictionary=Dictionary({"alpha": "beta"})
    ).apply()
    encoded = tmp_path / "original_safe.py"
    encoded.write_bytes(
        encoded.read_bytes().replace(b"x + 1", b"x * 2").replace(b"== 3", b"== 4")
    )
    return workspace, encoded, original


def snapshot(root):
    return {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_complete_agent_edit_loop_restores_and_executes_source(edit):
    workspace, encoded, original = edit
    before = snapshot(workspace.root)
    conflicted = restoration.prepare_restore(workspace, encoded, "original.py")
    report = conflicted.report()
    assert report["conflict_count"] == 1
    assert report["conflicts"][0]["id"] == "4:8"
    assert report["conflicts"][0]["translation"] == "alpha"
    assert snapshot(workspace.root) == before
    with pytest.raises(ValueError, match="Resolve every"):
        conflicted.apply(conflicted.review_sha256)
    choices = {
        "schema_version": 1,
        "edited_sha256": sha256(encoded.read_bytes()),
        "choices": {"4:8": "translate"},
    }
    (workspace.root / "choices.json").write_bytes(json_bytes(choices))
    plan = restoration.prepare_restore(
        workspace, encoded, "original.py", decisions="choices.json"
    )
    assert plan.report()["diff_complete"]
    saved = plan.apply(plan.review_sha256)
    assert saved["output_saved"] and saved["metadata_saved"]
    expected = original.replace(b"x + 1", b"x * 2").replace(b"== 3", b"== 4")
    assert plan.output.read_bytes() == expected
    subprocess.run([sys.executable, str(plan.output)], check=True, timeout=10)
    assert (
        restoration.verify_restoration(workspace, plan.output)["status"] == "verified"
    )
    for name, raw in before.items():
        assert (workspace.root / name).read_bytes() == raw
    # Strict decoding still detects edits.
    with pytest.raises(ValueError, match="Encoded file changed"):
        service.prepare(
            workspace, encoded, mode="reverse", output="strict.py", strict=True
        )


@pytest.mark.parametrize(
    "terms,expected", [("translate", "alpha"), ("literal", "beta")]
)
def test_explicit_term_policy_is_applied_only_to_changed_lines(edit, terms, expected):
    workspace, encoded, _ = edit
    plan = restoration.prepare_restore(workspace, encoded, "original.py", terms=terms)
    assert plan.result is not None
    assert b"def alpha(x):" in plan.result
    assert f"assert {expected}(2) == 4".encode() in plan.result
    assert b"# beta stays literal" in plan.result


@pytest.mark.parametrize(
    "changed", ["original.py", "original_safe.py", ".original_safe.py.scm_ledger.json"]
)
def test_all_stale_inputs_block_application(edit, changed):
    workspace, encoded, _ = edit
    plan = restoration.prepare_restore(
        workspace, encoded, "original.py", terms="translate"
    )
    path = workspace.root / changed
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed after review"):
        plan.apply(plan.review_sha256)
    assert not plan.output.exists()


def test_changed_decisions_and_wrong_review_digest_refused(edit):
    workspace, encoded, _ = edit
    choices = workspace.root / "choices.json"
    choices.write_bytes(
        json_bytes(
            {
                "schema_version": 1,
                "edited_sha256": sha256(encoded.read_bytes()),
                "choices": {"4:8": "translate"},
            }
        )
    )
    plan = restoration.prepare_restore(
        workspace, encoded, "original.py", decisions=choices
    )
    with pytest.raises(ValueError, match="Review digest"):
        plan.apply("0" * 64)
    choices.write_bytes(choices.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed after review"):
        plan.apply(plan.review_sha256)


@pytest.mark.parametrize("failure_kind", ["denied", "published_cleanup"])
def test_partial_receipt_failure_is_reported_and_explicitly_recovered(
    edit, monkeypatch, failure_kind
):
    workspace, encoded, _ = edit
    plan = restoration.prepare_restore(
        workspace, encoded, "original.py", terms="translate"
    )

    def fail(path, raw):
        if path == plan.metadata_path:
            if failure_kind == "published_cleanup":
                atomic_write(path, raw)
                raise restoration.PublicationError("retained metadata cleanup failure")
            raise PermissionError("controlled receipt failure")
        atomic_write(path, raw)

    with monkeypatch.context() as patch:
        patch.setattr(restoration, "atomic_write", fail)
        with pytest.raises(service.OperationError) as error:
            plan.apply(plan.review_sha256)
    assert error.value.report["output_saved"]
    assert error.value.report["metadata_saved"] == (failure_kind == "published_cleanup")
    if failure_kind == "denied":
        recovery = restoration.prepare_restore(
            workspace, encoded, "original.py", terms="translate", recover_metadata=True
        )
        assert recovery.review_sha256 == plan.review_sha256
        recovery.apply(plan.review_sha256, recover_metadata=True)
    assert (
        restoration.verify_restoration(workspace, plan.output)["status"] == "verified"
    )


@pytest.mark.parametrize(
    "malformation", ["stale_hash", "unknown_position", "invalid_choice"]
)
def test_invalid_decisions_are_actionable_and_read_only(edit, malformation):
    workspace, encoded, _ = edit
    document = {
        "schema_version": 1,
        "edited_sha256": sha256(encoded.read_bytes()),
        "choices": {"4:8": "translate"},
    }
    if malformation == "stale_hash":
        document["edited_sha256"] = "0" * 64
    elif malformation == "unknown_position":
        document["choices"] = {"900:1": "literal"}
    else:
        document["choices"] = {"4:8": "discard"}
    (workspace.root / "choices.json").write_bytes(json_bytes(document))
    before = snapshot(workspace.root)
    with pytest.raises(ValueError, match="decisions|Decisions"):
        restoration.prepare_restore(
            workspace, encoded, "original.py", decisions="choices.json"
        )
    assert snapshot(workspace.root) == before


def test_new_literal_and_stripped_marker_require_explicit_choice(edit):
    workspace, encoded, _ = edit
    encoded.write_bytes(
        encoded.read_bytes().replace(b"__SCM_LIT_beta__", b"beta")
        + b"# beta is a new literal\r\n"
    )
    report = restoration.prepare_restore(workspace, encoded, "original.py").report()
    assert report["conflict_count"] == 3
    assert {c["line"] for c in report["conflicts"]} == {4, 5, 6}
    choices = {
        c["id"]: "translate" if c["line"] == 4 else "literal"
        for c in report["conflicts"]
    }
    (workspace.root / "choices.json").write_bytes(
        json_bytes(
            {
                "schema_version": 1,
                "edited_sha256": sha256(encoded.read_bytes()),
                "choices": choices,
            }
        )
    )
    plan = restoration.prepare_restore(
        workspace, encoded, "original.py", decisions="choices.json"
    )
    assert plan.result is not None and b"assert alpha(2) == 4" in plan.result
    assert plan.result.endswith(b"# beta stays literal\r\n# beta is a new literal\r\n")


def test_damaged_markers_and_unsupported_case_do_not_silently_convert(edit):
    workspace, encoded, _ = edit
    raw = encoded.read_bytes()
    encoded.write_bytes(raw + b"# __SCM_LIT_broken__")
    with pytest.raises(ValueError, match="Damaged literal marker at line"):
        restoration.prepare_restore(
            workspace, encoded, "original.py", terms="translate"
        )
    encoded.write_bytes(raw + b"# bEtA")
    report = restoration.prepare_restore(
        workspace, encoded, "original.py", terms="translate"
    ).report()
    assert report["conflict_count"] == 1
    assert report["conflicts"][0]["choices"] == ["literal"]


@pytest.mark.parametrize("target", ["output", "receipt", "source"])
def test_no_clobber_and_retained_receipt_verification(edit, target):
    workspace, encoded, _ = edit
    plan = restoration.prepare_restore(
        workspace, encoded, "original.py", terms="translate"
    )
    plan.apply(plan.review_sha256)
    before = snapshot(workspace.root)
    with pytest.raises(FileExistsError):
        plan.apply(plan.review_sha256)
    assert snapshot(workspace.root) == before
    path = {"output": plan.output, "receipt": plan.metadata_path, "source": encoded}[
        target
    ]
    path.write_bytes(b"changed")
    with pytest.raises((ValueError, TypeError)):
        restoration.verify_restoration(workspace, plan.output)


def test_real_cli_review_digest_apply_and_verify(edit, capsys):
    workspace, _, _ = edit
    args = [
        "--root",
        str(workspace.root),
        "restore-edits",
        "original_safe.py",
        "--original",
        "original.py",
    ]
    assert main(args) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "conflict"
    assert main([*args, "--terms", "translate"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert main([*args, "--terms", "translate", "--apply"]) == 1
    capsys.readouterr()
    assert (
        main(
            [
                *args,
                "--terms",
                "translate",
                "--apply",
                "--review-sha256",
                report["review_sha256"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "saved"
    assert (
        main(["--root", str(workspace.root), "verify-restoration", report["output"]])
        == 0
    )


def test_mcp_conflict_no_token_and_reviewed_apply(edit):
    workspace, _, _ = edit

    async def exercise():
        async with Client(create_server(workspace.root)) as client:

            async def call(name, args):
                response = await client.call_tool(name, args)
                content = response.content[0]
                assert content.type == "text"
                return json.loads(content.text)

            arguments = {"source": "original_safe.py", "original": "original.py"}
            conflict = await call("preview_restoration", arguments)
            assert conflict["status"] == "conflict" and "plan_id" not in conflict
            review = await call(
                "preview_restoration", {**arguments, "terms": "translate"}
            )
            saved = await call("apply_preview", {"plan_id": review["plan_id"]})
            assert saved["metadata_saved"]
            assert (await call("verify_restored_file", {"source": saved["output"]}))[
                "status"
            ] == "verified"
            replay = await client.call_tool(
                "apply_preview", {"plan_id": review["plan_id"]}, raise_on_error=False
            )
            assert replay.is_error

    asyncio.run(exercise())


def test_added_duplicate_line_is_not_given_unlimited_baseline_provenance(edit):
    workspace, encoded, _ = edit
    encoded.write_bytes(encoded.read_bytes() + b"def beta(x):\r\n")
    report = restoration.prepare_restore(workspace, encoded, "original.py").report()
    assert {c["line"] for c in report["conflicts"]} == {4, 6}


@pytest.mark.parametrize(
    "output",
    [
        "original.py",
        "original_safe.py",
        ".original_safe.py.scm_ledger.json",
        "../escaped.py",
        ".mivim/protected.py",
    ],
)
def test_protected_or_conflicting_output_paths_are_rejected(edit, output):
    workspace, encoded, _ = edit
    before = snapshot(workspace.root)
    with pytest.raises((ValueError, FileExistsError)):
        restoration.prepare_restore(
            workspace, encoded, "original.py", output=output, terms="translate"
        )
    assert snapshot(workspace.root) == before


def test_wrong_original_and_corrupt_ledger_do_not_restore(edit):
    workspace, encoded, _ = edit
    (workspace.root / "wrong.py").write_bytes(b"different baseline")
    with pytest.raises(ValueError, match="Encoded file changed"):
        restoration.prepare_restore(workspace, encoded, "wrong.py", terms="translate")
    ledger = service.ledger_path(encoded)
    record = json.loads(ledger.read_bytes())
    record["dictionary_sha256"] = "0" * 64
    ledger.write_bytes(json_bytes(record))
    with pytest.raises(ValueError, match="dictionary digest"):
        restoration.prepare_restore(
            workspace, encoded, "original.py", terms="translate"
        )


def test_conflict_output_and_allocation_limits_are_explicit(edit, monkeypatch):
    workspace, encoded, _ = edit
    encoded.write_bytes(encoded.read_bytes() + b"# beta\r\n" * 130)
    report = restoration.prepare_restore(workspace, encoded, "original.py").report()
    assert report["conflict_count"] == 131 and len(report["conflicts"]) == 100
    assert report["omitted_conflicts"] == 31 and not report["ok"]
    monkeypatch.setattr(restoration, "MAX_CHOICES", 2)
    with pytest.raises(ValueError, match="changed-term limit"):
        restoration.prepare_restore(
            workspace, encoded, "original.py", terms="translate"
        )


def test_expanding_edited_terms_fail_before_output_allocation(tmp_path):
    workspace = Workspace(tmp_path, max_bytes=4096)
    (tmp_path / "original.txt").write_bytes(b"unchanged\n")
    service.prepare(
        workspace, "original.txt", dictionary=Dictionary({"a" * 100: "b"})
    ).apply()
    (tmp_path / "original_safe.txt").write_bytes(b"b " * 300)
    with pytest.raises(ValueError, match="workspace file limit"):
        restoration.prepare_restore(
            workspace, "original_safe.txt", "original.txt", terms="translate"
        )
    assert not (tmp_path / "original_safe_ready.txt").exists()


def test_deletion_and_line_ending_changes_preserve_requested_bytes(edit):
    workspace, encoded, original = edit
    changed = (
        encoded.read_bytes()
        .replace(b"# __SCM_LIT_beta__ stays literal\r\n", b"")
        .replace(b"\r\n", b"\n")
    )
    encoded.write_bytes(changed)
    plan = restoration.prepare_restore(
        workspace, encoded, "original.py", terms="translate"
    )
    expected = (
        original.replace(b"x + 1", b"x * 2")
        .replace(b"== 3", b"== 4")
        .replace(b"# beta stays literal\r\n", b"")
        .replace(b"\r\n", b"\n")
    )
    assert plan.result == expected


def test_output_changed_during_publication_retains_failure(edit, monkeypatch):
    workspace, encoded, _ = edit
    plan = restoration.prepare_restore(
        workspace, encoded, "original.py", terms="translate"
    )

    def race(path, raw):
        atomic_write(path, raw)
        if path == plan.output:
            path.write_bytes(b"concurrent edit")

    monkeypatch.setattr(restoration, "atomic_write", race)
    with pytest.raises(service.OperationError) as failure:
        plan.apply(plan.review_sha256)
    assert (
        failure.value.report["output_saved"]
        and not failure.value.report["metadata_saved"]
    )
    assert plan.output.read_bytes() == b"concurrent edit"
    with pytest.raises(ValueError, match="differs"):
        restoration.prepare_restore(
            workspace, encoded, "original.py", terms="translate", recover_metadata=True
        )


def test_mcp_exposes_partial_publication_and_recovers_it(edit, monkeypatch):
    workspace, _, _ = edit
    receipt = workspace.root / ".original_safe_ready.py.vigiles-restore.json"

    def fail(path, raw):
        if path == receipt:
            raise PermissionError("controlled MCP receipt failure")
        atomic_write(path, raw)

    async def exercise():
        async with Client(create_server(workspace.root)) as client:

            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                content = result.content[0]
                assert content.type == "text"
                return json.loads(content.text)

            arguments = {
                "source": "original_safe.py",
                "original": "original.py",
                "terms": "translate",
            }
            review = await call("preview_restoration", arguments)
            with monkeypatch.context() as patch:
                patch.setattr(restoration, "atomic_write", fail)
                failure = await call("apply_preview", {"plan_id": review["plan_id"]})
            assert failure["status"] == "error" and not failure["ok"]
            assert failure["output_saved"] and not failure["metadata_saved"]
            recovery = await call(
                "preview_restoration", {**arguments, "recover_metadata": True}
            )
            saved = await call("apply_preview", {"plan_id": recovery["plan_id"]})
            assert saved["status"] == "metadata_recovered" and saved["metadata_saved"]

    asyncio.run(exercise())
