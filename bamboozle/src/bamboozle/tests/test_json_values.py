"""Recursive JSON translation across saved source/agent/return boundaries."""

import copy
import json
import runpy
from pathlib import Path

import pytest

from bamboozle import formats, translate
from bamboozle.agent_files import AgentFiles
from bamboozle.agent_policy import AgentPolicy
from bamboozle.dictionary import Dictionary
from bamboozle.formats import DocumentCodec, FormatRules, format_options
from bamboozle.restoration import prepare_restore, verify_restoration
from bamboozle.return_validation import inspect_return
from bamboozle.service import ledger_path, prepare, recover, verify
from bamboozle.utils import json_bytes
from bamboozle.workspace import Workspace

MAPPING = Dictionary({"alpha": "omega", "task": "stage"})
RULES = FormatRules("json", values_only=True)


def test_recursive_strings_keep_exact_keys_tokens_and_private_escape_context():
    raw = '\ufeff{\r\n "\\u0061lpha": ["\\u0061lpha", {"a/b":{"~key":"task omega"}}], "task": 1e+09, "zero":-0, "yes":true, "nil":null, "empty":{}\r\n}\r\n'
    codec = DocumentCodec(MAPPING, RULES)
    result = codec.transform(raw)
    assert result.text == raw.replace('"\\u0061lpha", {', '"omega", {').replace(
        '"task omega"', '"stage __SCM_LIT_omega__"'
    )
    assert set(result.context) == {"/alpha/0", "/alpha/1/a~1b/~0key"}
    assert codec.transform(result.text, "reverse", context=result.context).text == raw
    assert codec.rules.document() == {"format": "json", "values_only": True}
    assert FormatRules.from_document(codec.rules.document()) == RULES
    assert format_options(None, None, values_only=True) == RULES
    assert FormatRules("json", ("/x",)).document() == {
        "format": "json",
        "pointers": ["/x"],
    }


@pytest.mark.parametrize(
    "raw",
    [
        '"alpha"',
        '""',
        "[]",
        "{}",
        "123.40",
        "null",
        "true",
        '[["Alpha", [], {"alpha":"aLpHa"}]]',
    ],
)
@pytest.mark.parametrize("marker_version", [1, 2])
def test_roots_empty_documents_and_legacy_markers_roundtrip(raw, marker_version):
    codec = DocumentCodec(MAPPING, RULES, marker_version=marker_version)
    encoded = codec.transform(raw)
    assert codec.transform(encoded.text, "reverse", context=encoded.context).text == raw


@pytest.mark.parametrize(
    "rule",
    [
        {"format": "json", "values_only": False},
        {"format": "json", "values_only": 1},
        {"format": "json", "values_only": "true"},
        {"format": "json", "values_only": True, "pointers": []},
        {"format": "text", "values_only": True},
        {"format": "yaml", "values_only": True},
        {"format": "json", "values_only": True, "protect": ["urls"]},
    ],
)
def test_noncanonical_or_mixed_selections_refuse(rule):
    with pytest.raises(ValueError):
        FormatRules.from_document(rule)


def test_cli_selection_conflicts_are_explicit():
    for fmt, pointers in (("text", None), ("yaml", None), ("json", ["/x"])):
        with pytest.raises(ValueError):
            format_options(fmt, pointers, values_only=True)
    with pytest.raises(ValueError):
        FormatRules("json", ("/x",), values_only=True)


def test_dynamic_values_removal_and_type_changes_decode_under_retained_rules():
    codec = DocumentCodec(MAPPING, RULES)
    encoded = codec.transform(
        '{"alpha":"\\u0061lpha","items":["task", "omega"],"flag":false}'
    )
    edited = encoded.text.replace(
        '"items":["stage", "__SCM_LIT_omega__"]',
        '"items":["stage new", "__SCM_LIT_omega__", {"task":"omega stage"}]',
    ).replace('"flag":false', '"flag":false,"extra":"stage"')
    restored = codec.transform(edited, "reverse", context=encoded.context).text
    assert '"alpha":"\\u0061lpha"' in restored
    assert json.loads(restored)["items"] == [
        "task new",
        "omega",
        {"task": "alpha task"},
    ]
    assert json.loads(restored)["extra"] == "task"
    changed_type = codec.transform('{"alpha":2}', "reverse", context=encoded.context)
    assert changed_type.text == '{"alpha":2}'


def test_diagnostics_preserve_key_and_type_structure_but_ignore_selected_prose():
    codec = DocumentCodec(MAPPING, RULES)
    baseline = codec.transform('{"alpha":"alpha","number":2,"empty":[]}')

    def review(edited):
        return inspect_return(
            edited.encode(),
            MAPPING,
            baseline=baseline.text.encode(),
            rules=RULES,
            context=baseline.context,
        )

    assert review(baseline.text)["status"] == "accepted"
    assert (
        "protected_json_changed"
        not in review(baseline.text.replace('"omega"', '"omega processed"'))["counts"]
    )
    for edited in (
        '{"renamed":"omega","number":2,"empty":[]}',
        '{"alpha":4,"number":2,"empty":[]}',
        '{"alpha":"omega","number":3,"empty":[]}',
        '{"alpha":"omega","number":2,"empty":{}}',
        '{"alpha":"omega","number":2,"empty":[],"new":"stage"}',
    ):
        result = review(edited)
        assert result["decodable"] and result["counts"]["protected_json_changed"] >= 1


def test_bounds_support_more_than_exact_pointer_limit_and_refuse_excess(monkeypatch):
    codec = DocumentCodec(MAPPING, RULES)
    source = json.dumps([{"alpha": "alpha", "count": index} for index in range(600)])
    encoded = codec.transform(source)
    assert len(encoded.context) == 600
    assert (
        codec.transform(encoded.text, "reverse", context=encoded.context).text == source
    )
    monkeypatch.setattr(formats, "MAX_JSON_VALUES", 2)
    with pytest.raises(ValueError, match="budget"):
        codec.transform('["alpha","alpha","alpha"]')
    with pytest.raises(ValueError, match="limit"):
        codec.validate_context(encoded.context)
    monkeypatch.setattr(formats, "MAX_JSON_POINTER_CHARS", 4)
    with pytest.raises(ValueError, match="budget"):
        codec.transform('{"long-key":"alpha"}')


@pytest.mark.parametrize(
    "raw",
    [
        '{"x":"alpha","x":"task"}',
        "[NaN]",
        '{"x":Infinity}',
        '{"x": "alpha",}',
        '"\\ud800"',
        '{"\\ud800":false}',
        '{"' + "x" * 2049 + '":"alpha"}',
        "[" * 66 + '"alpha"' + "]" * 66,
    ],
)
def test_invalid_or_unrepresentable_recursive_input_is_refused_before_publication(
    raw, tmp_path
):
    (tmp_path / "input.json").write_bytes(raw.encode())
    with pytest.raises(ValueError):
        prepare(
            Workspace(tmp_path),
            "input.json",
            output="out.json",
            dictionary=MAPPING,
            format_rules=RULES,
        )
    assert not (tmp_path / "out.json").exists()


@pytest.mark.parametrize("pointer", ["bad", "/bad~2", "/" + "x" * 2049, 4])
def test_bad_context_pointers_are_not_accepted(pointer):
    codec = DocumentCodec(MAPPING, RULES)
    encoded = codec.transform('{"x":"alpha"}')
    context = {pointer: encoded.context["/x"]}
    with pytest.raises(ValueError):
        codec.transform(encoded.text, "reverse", context=context)


def test_context_dictionary_tampering_and_malformed_return_marker_refuse():
    codec = DocumentCodec(MAPPING, RULES)
    encoded = codec.transform('{"x":"alpha"}')
    context = copy.deepcopy(encoded.context)
    context["/x"]["original"] = '"task"'
    with pytest.raises(ValueError, match="dictionary"):
        codec.transform(encoded.text, "reverse", context=context)
    with pytest.raises(ValueError, match="marker"):
        codec.transform(
            '{"x":"__SCM_CASE_invalid__"}', "reverse", context=encoded.context
        )


def test_array_insertions_restore_unambiguous_moved_spellings_without_guessing_identity():
    codec = DocumentCodec(MAPPING, RULES)
    encoded = codec.transform('["\\u0061lpha", "task"]')
    moved = '["stage new", ' + encoded.text[1:]
    assert (
        codec.transform(moved, "reverse", context=encoded.context).text
        == '["task new", "\\u0061lpha", "task"]'
    )
    ambiguous = codec.transform('["alpha", "\\u0061lpha"]')
    moved = '{"new":' + ambiguous.text + "}"
    assert (
        codec.transform(moved, "reverse", context=ambiguous.context).text
        == '{"new":["alpha", "alpha"]}'
    )
    exact = DocumentCodec(MAPPING, FormatRules("json", ("/0", "/1")))
    before = exact.transform('["\\u0061lpha", "task"]')
    assert (
        exact.transform('["stage", "omega"]', "reverse", context=before.context).text
        == '["task", "alpha"]'
    )


def test_manual_recovery_restoration_and_compatibility_commands(tmp_path):
    workspace = Workspace(tmp_path)
    raw = b'{"alpha":"\\u0061lpha","values":["task"], "count":2}\r\n'
    (tmp_path / "source.json").write_bytes(raw)
    (tmp_path / "mapping.dictionary.json").write_bytes(json_bytes(MAPPING.document()))
    assert (
        translate.main(
            [
                "--root",
                str(tmp_path),
                "--input",
                "source.json",
                "--output",
                "neutral.json",
                "--mode",
                "forward",
                "--dict",
                "mapping.dictionary.json",
                "--json-values-only",
            ]
        )
        == 0
    )
    metadata = ledger_path(tmp_path / "neutral.json")
    metadata.unlink()
    assert recover(
        workspace,
        "neutral.json",
        "source.json",
        MAPPING,
        apply=True,
        format_rules=RULES,
    )["ledger_saved"]
    assert verify(workspace, "neutral.json")["status"] == "verified"
    (tmp_path / "returned.json").write_bytes(
        (tmp_path / "neutral.json")
        .read_bytes()
        .replace(b'["stage"]', b'["stage", "omega stage"]')
    )
    plan = prepare_restore(
        workspace,
        "returned.json",
        "source.json",
        output="restored.json",
        ledger=metadata,
        terms="translate",
    )
    plan.apply(plan.review_sha256)
    assert (tmp_path / "restored.json").read_bytes() == raw.replace(
        b'["task"]', b'["task", "alpha task"]'
    )
    assert verify_restoration(workspace, "restored.json")["status"] == "verified"
    with pytest.raises(ValueError, match="saved|match"):
        prepare(
            workspace,
            "neutral.json",
            mode="reverse",
            format_rules=FormatRules("json", ("/alpha",)),
        )


def test_source_agent_reconnect_and_policy_drift_new_files(tmp_path):
    workspace = Workspace(tmp_path)
    (tmp_path / "data.json").write_text('{"alpha":"alpha", "records":["task"]}')
    policy = {
        "schema_version": 2,
        "files": {"data.json": {"policy": "translate", "document": RULES.document()}},
        "new_files": {
            "generated": {"policy": "translate", "document": RULES.document()}
        },
    }
    (tmp_path / "source.agent-policy.json").write_bytes(json_bytes(policy))

    def connect():
        return AgentFiles(
            workspace,
            MAPPING,
            policy=AgentPolicy(workspace, "source.agent-policy.json"),
        )

    read = connect().read("data.json")
    assert read["document_rules"] == RULES.document()
    service = connect()
    service.write(
        "data.json",
        read["content"].replace('["stage"]', '["stage", "omega stage"]'),
        read["source_sha256"],
        read["context_sha256"],
    )
    service.write("generated/data.json", '{"task":[{"alpha":"omega stage"}]}')
    assert json.loads((tmp_path / "generated/data.json").read_bytes()) == {
        "task": [{"alpha": "alpha task"}]
    }
    stale = service.read("data.json")
    policy["files"]["data.json"]["document"] = {
        "format": "json",
        "pointers": ["/alpha"],
    }
    (tmp_path / "source.agent-policy.json").write_bytes(json_bytes(policy))
    with pytest.raises(ValueError, match="context"):
        connect().write(
            "data.json",
            stale["content"],
            stale["source_sha256"],
            stale["context_sha256"],
        )


def test_complete_json_values_application(tmp_path):
    run = runpy.run_path(
        str(Path(__file__).parents[3] / "scripts/json_values_smoke.py")
    )["run"]
    result = run(tmp_path / "applications")
    assert result["status"] == "passed"
    assert set(result["workflows"]) == {"cli", "mcp", "agent"}
