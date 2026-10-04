"""Returned vocabulary/structure review through actual restoration and view workflows."""

import asyncio
import json
import subprocess
import sys

import pytest
from fastmcp import Client

from bamboozle import return_validation as checks
from bamboozle import view_import
from bamboozle.cli import main
from bamboozle.dictionary import Dictionary
from bamboozle.formats import DocumentCodec, FormatRules
from bamboozle.mcp import create_server
from bamboozle.project_views import export_view
from bamboozle.restoration import prepare_restore, verify_restoration
from bamboozle.scm_mcp import create_server as create_agent
from bamboozle.service import OperationError, prepare
from bamboozle.utils import atomic_write, json_bytes, sha256
from bamboozle.workspace import Workspace

MAPPING = Dictionary({"fuck": "assemble", "shit": "residue", "fucks": "builds"})


def inspect(text, baseline=None, mapping=MAPPING, **kwargs):
    return checks.inspect_return(
        text.encode(),
        mapping,
        baseline=baseline.encode() if baseline is not None else None,
        **kwargs,
    )


def codes(report):
    return {item["code"] for item in report["findings"]}


@pytest.mark.parametrize(
    "source",
    [
        "fuck shit assemble\r\n",
        "fUcK residue\n",
        "FUCK SHIT\r",
        "déjà vu fuck\n",
        "",
        "fuck assemble fuck\n",
    ],
)
def test_exact_saved_returns_are_accepted_including_literals_case_and_unicode(source):
    encoded = DocumentCodec(MAPPING).transform(source).text
    result = inspect(encoded, encoded)
    assert result["status"] == "accepted" and result["analysis_complete"]
    assert result["decoded_sha256"] == sha256(source.encode())
    assert result["finding_count"] == 0


def test_changed_line_target_requires_intent_and_marked_literal_resolves_it():
    report = inspect("Use assemble here.\n", "Use something here.\n")
    finding = report["findings"][0]
    assert report["status"] == "needs_review" and report["decodable"]
    assert finding["code"] == "ambiguous_target" and finding["source_term"] == "fuck"
    assert finding["location"] == {"offset": 4, "line": 1, "column": 5}
    assert finding["literal_marker"] == "__SCM_LIT_assemble__"
    assert (
        inspect("Use __SCM_LIT_assemble__ here.\n", "Use something here.\n")["status"]
        == "accepted"
    )


@pytest.mark.parametrize(
    "word,base",
    [
        ("assembles", "assemble"),
        ("assembling", "assemble"),
        ("assembled", "assemble"),
        ("assembler", "assemble"),
        ("assemblers", "assemble"),
        ("residues", "residue"),
        ("fucking", "fuck"),
        ("fucked", "fuck"),
        ("fucker", "fuck"),
    ],
)
def test_unlisted_inflections_are_located_but_never_silently_translated(word, base):
    result = inspect(f"new {word}\n")
    finding = result["findings"][0]
    assert codes(result) == {"unlisted_variant"}
    assert finding["location"]["column"] == 5
    assert base in {candidate["term"] for candidate in finding["candidates"]}
    assert result["decoded_sha256"] == sha256(f"new {word}\n".encode())


@pytest.mark.parametrize(
    "word,base",
    [
        ("carried", "carry"),
        ("carriers", "carrier"),
        ("running", "run"),
        ("happiest", "happy"),
    ],
)
def test_suffix_hints_handle_declared_ascii_stems_only(word, base):
    result = inspect(word, mapping=Dictionary({"source": base}))
    assert result["counts"] == {"unlisted_variant": 1}
    assert result["decoded_sha256"] == sha256(word.encode())


@pytest.mark.parametrize(
    "text",
    [
        "assembler_value",
        "assembler2",
        "éassembling",
        "assembling\u0301",
        "preassemble",
        "ASSEMBLY",
        "assembly",
        "httpsanything",
    ],
)
def test_identifier_boundaries_and_unknown_synonyms_do_not_invent_variants(text):
    assert inspect(text)["status"] == "accepted"


def test_explicit_forms_take_precedence_over_heuristic_candidates():
    result = inspect("builds")
    assert codes(result) == {"ambiguous_target"}
    assert result["decoded_sha256"] == sha256(b"fucks")
    assert inspect("assembled", "assembled")["status"] == "accepted"


def test_real_dictionary_underscore_forms_are_checked_without_splitting_identifiers():
    dictionary = Dictionary.load()
    report = inspect(
        "kinetic_impulseful kinetic_impulsing kinetic_impulses", mapping=dictionary
    )
    variant = next(
        item for item in report["findings"] if item["code"] == "unlisted_variant"
    )
    assert variant["term"] == "kinetic_impulsing"
    assert {item["term"] for item in variant["candidates"]} == {"kinetic_impulse"}
    assert report["counts"]["ambiguous_target"] == 1
    assert not any(
        item.get("term") == "kinetic_impulseful" for item in report["findings"]
    )


def test_escaped_surrogates_in_unselected_json_remain_opaque_and_reviewable():
    before = '{"prompt":"assemble","opaque":"\\ud800"}'
    after = '{"prompt":"assemble","opaque":"\\ud801"}'
    rules = FormatRules("json", ("/prompt",))
    assert inspect(before, before, rules=rules)["status"] == "accepted"
    assert inspect(after, before, rules=rules)["counts"] == {
        "protected_json_changed": 1
    }


def test_removal_and_source_domain_drift_report_evidence_without_guessing_synonyms():
    report = inspect("fuck machinery\n", "assemble residue\n")
    assert codes(report) == {"source_term", "controlled_term_removed"}
    removed = [
        item for item in report["findings"] if item["code"] == "controlled_term_removed"
    ]
    assert {item["neutral_term"] for item in removed} == {"assemble", "residue"}
    assert report["decoded_sha256"] == sha256(b"fuck machinery\n")
    assert inspect("new synonym\n", "unmapped old text\n")["status"] == "accepted"


def test_removed_or_replaced_literal_and_case_markers_are_explicit_review_findings():
    baseline = DocumentCodec(MAPPING).transform("fUcK assemble").text
    report = inspect("assemble assemble", baseline)
    assert report["counts"]["marker_removed"] == 2
    assert report["counts"]["ambiguous_target"] == 2
    assert report["status"] == "needs_review"


def test_phrase_diagnostics_follow_the_same_longest_match_as_decoding():
    mapping = Dictionary({"source phrase": "fluid exchange", "source": "fluid"})
    result = inspect("fluid exchange and fluid", mapping=mapping)
    assert [finding["term"] for finding in result["findings"]] == [
        "fluid exchange",
        "fluid",
    ]
    assert result["decoded_sha256"] == sha256(b"source phrase and source")


@pytest.mark.parametrize(
    "marker",
    [
        "__SCM_LIT_nope__",
        "__SCM_LIT_assemble_",
        "__SCM_CASE_ffff_assemble__",
        "__SCM_CASE_1_ASSEMBLE__",
        "__SCM_CASE_z_assemble__",
    ],
)
def test_malformed_markers_refuse_with_precise_document_location(marker):
    report = inspect("one\r\ntwo " + marker)
    assert report["status"] == "refused" and not report["decodable"]
    assert "invalid_marker" in codes(report)
    finding = next(
        item for item in report["findings"] if item["code"] == "invalid_marker"
    )
    assert finding["location"] == {"offset": 9, "line": 2, "column": 5}


def test_mixed_case_unmarked_target_is_refused_without_weakened_decode():
    report = inspect("aSsEmBle")
    assert report["status"] == "refused" and "decode_error" in codes(report)
    assert inspect("__SCM_LIT_aSsEmBle__")["decoded_sha256"] == sha256(b"aSsEmBle")


def test_unchanged_lines_can_move_but_extra_duplicates_still_require_review():
    assert inspect("B\nassemble\nA\n", "A\nassemble\nB\n")["status"] == "accepted"
    report = inspect("assemble\nassemble\n", "assemble\n")
    assert report["counts"] == {"ambiguous_target": 1}
    assert report["findings"][0]["location"]["line"] == 2


def test_json_locations_use_decoded_value_positions_and_raw_token_pointer():
    rules = FormatRules("json", ("/a~1b/~0prompt",))
    before = '{"a/b":{"~prompt":"ordinary"},"id":"assemble"}'
    after = (
        '{\r\n "a/b":{"~prompt":"line\\n\\u0061ssembles assemble"},"id":"assemble"\r\n}'
    )
    result = inspect(after, before, rules=rules)
    assert codes(result) == {"unlisted_variant", "ambiguous_target"}
    variant = next(
        item for item in result["findings"] if item["code"] == "unlisted_variant"
    )
    assert variant["location"]["pointer"] == "/a~1b/~0prompt"
    assert variant["location"]["token"]["line"] == 2
    assert variant["location"]["value"] == {"offset": 5, "line": 2, "column": 1}


def test_json_keys_unselected_values_types_and_arrays_are_reviewed_independently():
    rules = FormatRules("json", ("/prompt",))
    result = inspect(
        '{"prompt":"assemble", "id":true, "arr":[1,3], "new":null}',
        '{"prompt":"assemble", "id":1,"arr":[1,2],"old":null}',
        rules=rules,
    )
    assert codes(result) == {"protected_json_changed"}
    assert {item["pointer"] for item in result["findings"]} == {
        "/id",
        "/arr/1",
        "/new",
        "/old",
    }
    assert (
        inspect(
            '{ "prompt": "assemble", "id":"\\u0061" }',
            '{"id":"a","prompt":"assemble"}',
            rules=rules,
        )["status"]
        == "accepted"
    )


@pytest.mark.parametrize(
    "returned",
    [
        '{"prompt":false}',
        '{"other":"assemble"}',
        '{"prompt":"a","prompt":"b"}',
        '{"prompt":"a", "id":NaN}',
        "{bad",
        "[]",
    ],
)
def test_invalid_selected_json_is_refused(returned):
    report = inspect(
        returned, '{"prompt":"assemble"}', rules=FormatRules("json", ("/prompt",))
    )
    assert report["status"] == "refused" and "invalid_document" in codes(report)


def test_markdown_protected_literals_are_not_vocabulary_and_prose_still_is():
    rules = FormatRules("markdown")
    baseline = (
        "Ordinary prose.\n\n`assemble`\n\n```python\n__SCM_LIT_broken__ = 1\n```\n"
    )
    result = inspect(baseline.replace("Ordinary", "assembling"), baseline, rules=rules)
    assert codes(result) == {"unlisted_variant"}
    assert result["status"] == "needs_review" and result["decodable"]
    changed = baseline.replace("= 1", "= 2").replace("`assemble`", "`residue`")
    reviewed = inspect(changed, baseline, rules=rules)
    assert codes(reviewed) == {"protected_markdown_changed"}
    assert reviewed["counts"]["protected_markdown_changed"] == 4


def test_markdown_protected_order_changes_are_visible():
    report = inspect(
        "`two` and `one`\n", "`one` and `two`\n", rules=FormatRules("markdown")
    )
    assert codes(report) == {"protected_markdown_reordered"}


def test_json_selected_pointer_scoping_does_not_borrow_baseline_terms_from_other_fields():
    rules = FormatRules("json", ("/left", "/right"))
    result = inspect(
        '{"left":"plain", "right":"assemble"}',
        '{"left":"assemble", "right":"plain"}',
        rules=rules,
    )
    assert result["counts"] == {"ambiguous_target": 1}
    assert result["findings"][0]["location"]["pointer"] == "/right"


def test_all_pages_share_input_digest_and_exhaust_findings_without_changing_counts():
    returned = "new assemble\n" * 105
    first = inspect(returned, max_findings=17)
    records = []
    page = first
    while True:
        records.extend(page["findings"])
        assert page["validation_sha256"] == first["validation_sha256"]
        assert page["finding_count"] == 105 and page["analysis_complete"]
        if page["next_offset"] is None:
            break
        page = inspect(returned, max_findings=17, offset=page["next_offset"])
    assert [item["index"] for item in records] == list(range(105))
    assert (
        inspect(returned + "!", max_findings=17)["validation_sha256"]
        != first["validation_sha256"]
    )


def test_large_analysis_reports_incomplete_but_retains_real_decode_result():
    returned = "x" * (checks.MAX_ANALYSIS_CHARS + 1)
    report = inspect(returned)
    assert report["status"] == "needs_review" and not report["analysis_complete"]
    assert report["decodable"] and codes(report) == {"analysis_limit"}
    assert inspect("assemble", limit=2)["status"] == "refused"
    assert checks.inspect_return(b"\xff", MAPPING)["status"] == "refused"


@pytest.mark.parametrize(
    "args",
    [
        {"offset": -1},
        {"offset": True},
        {"max_findings": 501},
        {"max_findings": True},
        {"max_findings": -1},
    ],
)
def test_invalid_pagination_is_refused(args):
    with pytest.raises(ValueError):
        inspect("", **args)


@pytest.fixture
def single(tmp_path):
    workspace = Workspace(tmp_path)
    original = b"\xef\xbb\xbfdef fuck(values):\r\n    return sum(values)\r\nassert fuck([1, 2]) == 3\r\n"
    (tmp_path / "source.py").write_bytes(original)
    prepare(workspace, "source.py", dictionary=MAPPING).apply()
    return workspace, tmp_path / "source_safe.py", original


def test_single_file_saved_context_review_apply_and_actual_execution(single):
    workspace, returned, original = single
    returned.write_bytes(
        returned.read_bytes()
        .replace(b"sum(values)", b"sum(v * v for v in values)")
        .replace(b"== 3", b"== 5")
    )
    report = checks.validate_return(workspace, returned, "source.py")
    assert report["counts"] == {"ambiguous_target": 1}
    assert report["dictionary_sha256"] == MAPPING.fingerprint
    plan = prepare_restore(workspace, returned, "source.py")
    assert not plan.report()["ok"]
    assert (
        plan.report()["return_validation"]["validation_sha256"]
        == report["validation_sha256"]
    )
    plan = prepare_restore(workspace, returned, "source.py", terms="translate")
    assert plan.report()["return_validation"]["status"] == "needs_review"
    saved = plan.apply(plan.review_sha256)
    assert (
        saved["metadata_saved"]
        and verify_restoration(workspace, plan.output)["status"] == "verified"
    )
    subprocess.run([sys.executable, str(plan.output)], check=True, timeout=10)
    assert (workspace.root / "source.py").read_bytes() == original


def test_manual_literal_intent_is_preserved_despite_advisory_translation_hash(single):
    workspace, returned, _ = single
    returned.write_bytes(returned.read_bytes() + b"# assemble means a literal here\r\n")
    plan = prepare_restore(workspace, returned, "source.py", terms="literal")
    assert plan.result is not None and plan.result.endswith(
        b"# assemble means a literal here\r\n"
    )
    assert plan.report()["return_validation"]["status"] == "needs_review"
    plan.apply(plan.review_sha256)
    assert verify_restoration(workspace, plan.output)["status"] == "verified"


@pytest.mark.parametrize(
    "rules,source",
    [
        (FormatRules("json", ("/prompt",)), '{"prompt":"fuck", "id":"assemble"}'),
        (FormatRules("markdown"), "fuck\n\n`assemble`\n"),
    ],
)
def test_single_file_uses_saved_structured_context_even_when_other_dictionary_exists(
    tmp_path, rules, source
):
    workspace = Workspace(tmp_path)
    (tmp_path / "source.txt").write_bytes(source.encode())
    prepare(workspace, "source.txt", dictionary=MAPPING, format_rules=rules).apply()
    (tmp_path / "dictionary.json").write_bytes(json_bytes({"unrelated": "mapping"}))
    report = checks.validate_return(workspace, "source_safe.txt", "source.txt")
    assert (
        report["status"] == "accepted" and report["document_rules"] == rules.document()
    )


@pytest.mark.parametrize(
    "input_name", ["source.py", "source_safe.py", ".source_safe.py.scm_ledger.json"]
)
def test_changed_inputs_during_read_only_validation_refuse(
    single, monkeypatch, input_name
):
    workspace, _, _ = single
    original_inspect = checks.inspect_return

    def race(*args, **kwargs):
        report = original_inspect(*args, **kwargs)
        path = workspace.root / input_name
        path.write_bytes(path.read_bytes() + b" ")
        return report

    monkeypatch.setattr(checks, "inspect_return", race)
    with pytest.raises(ValueError, match="changed during inspection"):
        checks.validate_return(workspace, "source_safe.py", "source.py")


def test_read_only_cli_and_operator_mcp_reports_agree_and_agent_has_no_validator(
    single, capsys
):
    workspace, returned, _ = single
    returned.write_bytes(returned.read_bytes() + b"# assembling assemble\n")
    before = {
        path.name: path.read_bytes()
        for path in workspace.root.iterdir()
        if path.is_file()
    }
    assert (
        main(
            [
                "--root",
                str(workspace.root),
                "validate-return",
                "source_safe.py",
                "--original",
                "source.py",
                "--max-findings",
                "1",
            ]
        )
        == 3
    )
    cli = json.loads(capsys.readouterr().out)
    assert cli["next_offset"] == 1
    assert "plan_id" not in cli and cli["output_saved"] is False

    async def exercise():
        async with Client(create_server(workspace.root)) as client:
            result = await client.call_tool(
                "validate_returned_file",
                {
                    "source": "source_safe.py",
                    "original": "source.py",
                    "max_findings": 1,
                },
            )
            assert not result.is_error
            assert result.data == cli
        async with Client(create_agent(workspace.root)) as agent:
            assert {tool.name for tool in await agent.list_tools()} == {
                "read_safe_file",
                "write_safe_file",
            }

    asyncio.run(exercise())
    assert before == {
        path.name: path.read_bytes()
        for path in workspace.root.iterdir()
        if path.is_file()
    }


def test_wrong_retained_original_cannot_authorize_return_validation(single):
    workspace, _, _ = single
    (workspace.root / "source.py").write_bytes(b"different original")
    with pytest.raises(ValueError, match="strict restoration refused"):
        checks.validate_return(workspace, "source_safe.py", "source.py")


@pytest.fixture
def view(tmp_path):
    workspace = Workspace(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/core.py").write_text(
        "def fuck(values):\n    return sum(values)\n", encoding="utf-8"
    )
    (tmp_path / "config.json").write_bytes(b'{"prompt":"fuck", "limit":2}')
    (tmp_path / "guide.md").write_bytes(b"fuck\n\n`python src/core.py`\n")
    policy = {
        "schema_version": 2,
        "files": {
            "src/core.py": {"policy": "translate"},
            "config.json": {
                "policy": "translate",
                "document": {"format": "json", "pointers": ["/prompt"]},
            },
            "guide.md": {"policy": "translate", "document": {"format": "markdown"}},
        },
        "new_files": {"src": "translate"},
    }
    (tmp_path / "policy.json").write_bytes(json_bytes(policy))
    exported = export_view(workspace, "policy.json", "view", MAPPING)
    return workspace, exported["view_id"], tmp_path / "view"


def edit_view(root):
    (root / "src/core.py").write_text(
        "def assemble(values):\n    if not values:\n        raise ValueError('samples required')\n    return sum(v * v for v in values)\n",
        encoding="utf-8",
    )
    (root / "src/check.py").write_text(
        "from core import assemble\nassert assemble([3, 4]) == 25\ntry:\n    assemble([])\nexcept ValueError:\n    pass\nelse:\n    raise AssertionError('missing error')\nprint('returned-pipeline-ok')\n",
        encoding="utf-8",
    )
    (root / "config.json").write_bytes(b'{"prompt":"assemble", "limit":5}')
    (root / "guide.md").write_bytes(b"assemble\n\n`python src/check.py`\n")


def test_view_diagnostics_pagination_preserves_review_digest_and_executable_return(
    view,
):
    workspace, view_id, root = view
    edit_view(root)
    review = view_import.review_import(workspace, view_id)
    assert review["ok"] and review["return_validation"]["status"] == "needs_review"
    reports = {item["path"]: item for item in review["return_validation"]["files"]}
    assert reports["src/check.py"]["baseline_available"] is False
    assert reports["config.json"]["counts"] == {"protected_json_changed": 1}
    assert reports["guide.md"]["counts"] == {"protected_markdown_changed": 2}
    first = view_import.review_import(
        workspace, view_id, validation_path="src/check.py", max_findings=1
    )
    second = view_import.review_import(
        workspace,
        view_id,
        validation_path="src/check.py",
        validation_offset=1,
        max_findings=1,
    )
    assert first["review_sha256"] == second["review_sha256"] == review["review_sha256"]
    assert first["return_validation"]["files"][0]["findings"][0]["index"] == 0
    assert second["return_validation"]["files"][0]["findings"][0]["index"] == 1
    # New local policy/dictionary cannot reinterpret the originating snapshot.
    (workspace.root / "policy.json").write_bytes(b"changed local config")
    view_import.apply_import(workspace, view_id, review["review_sha256"])
    subprocess.run(
        [sys.executable, str(workspace.root / "src/check.py")], check=True, timeout=10
    )
    assert "from core import fuck" in (workspace.root / "src/check.py").read_text()
    assert view_import.view_status(workspace, view_id)["status"] == "complete"


def test_view_decode_refusal_withholds_digest(view):
    workspace, view_id, root = view
    (root / "src/core.py").write_bytes(b"__SCM_LIT_damaged__")
    review = view_import.review_import(workspace, view_id)
    assert not review["ok"] and review["review_sha256"] is None
    assert review["return_validation"]["status"] == "refused"


def test_view_report_budget_preserves_counts_and_allows_reading_omitted_file(view):
    workspace, view_id, root = view
    for name in ("alpha.py", "beta.py", "gamma.py"):
        (root / "src" / name).write_bytes(b"# assemble\n" * 250)
    report = view_import.review_import(workspace, view_id)
    validation = report["return_validation"]
    assert validation["finding_count"] == 750 and not validation["findings_complete"]
    assert sum(len(item["findings"]) for item in validation["files"]) == 200
    page = view_import.review_import(
        workspace,
        view_id,
        validation_path="src/gamma.py",
        validation_offset=200,
        max_findings=50,
    )
    assert page["review_sha256"] == report["review_sha256"]
    assert [
        item["index"] for item in page["return_validation"]["files"][0]["findings"]
    ] == list(range(200, 250))


@pytest.mark.parametrize(
    "options",
    [
        {"validation_offset": 1},
        {"validation_path": "../source.py"},
        {"validation_path": "missing.py"},
        {"validation_path": "src/core.py", "max_findings": 0},
    ],
)
def test_view_pagination_refuses_invalid_or_ambiguous_selection(view, options):
    workspace, view_id, _ = view
    with pytest.raises(ValueError):
        view_import.review_import(workspace, view_id, **options)


def test_stale_view_diagnostics_refuse_before_first_write(view):
    workspace, view_id, root = view
    edit_view(root)
    report = view_import.review_import(workspace, view_id)
    (root / "src/check.py").write_bytes(b"changed after review")
    with pytest.raises(OperationError, match="stale"):
        view_import.apply_import(workspace, view_id, report["review_sha256"])
    assert (workspace.root / "src/core.py").read_text().endswith("return sum(values)\n")


def test_review_detects_mid_inspection_view_change(view, monkeypatch):
    workspace, view_id, root = view
    real_inspect = view_import.inspect_return

    def race(*args, **kwargs):
        report = real_inspect(*args, **kwargs)
        (root / "src/core.py").write_bytes(b"changed")
        return report

    monkeypatch.setattr(view_import, "inspect_return", race)
    with pytest.raises(ValueError, match="changed during review"):
        view_import.review_import(workspace, view_id)


def test_existing_journal_recovery_does_not_depend_on_new_diagnostic_engine(
    view, monkeypatch
):
    workspace, view_id, root = view
    edit_view(root)
    report = view_import.review_import(workspace, view_id)

    def interrupt(path, data, **kwargs):
        if path == workspace.root / "src/check.py":
            raise OSError("retained partial return")
        return atomic_write(path, data, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(view_import, "atomic_write", interrupt)
        with pytest.raises(OperationError, match="retained partial"):
            view_import.apply_import(workspace, view_id, report["review_sha256"])

    def unavailable(*args, **kwargs):
        raise AssertionError(
            "Historical journal verification must not rerun diagnostics"
        )

    monkeypatch.setattr(view_import, "inspect_return", unavailable)
    view_import.apply_import(workspace, view_id, report["review_sha256"], recover=True)
    assert view_import.view_status(workspace, view_id)["status"] == "complete"
    subprocess.run(
        [sys.executable, str(workspace.root / "src/check.py")], check=True, timeout=10
    )
