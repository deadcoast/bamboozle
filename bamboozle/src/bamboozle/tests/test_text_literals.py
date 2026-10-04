"""Protected text must survive real source/agent/return workflows and bad input."""

import copy
import json
import runpy
from pathlib import Path

import pytest

from bamboozle import text_literals, view_import
from bamboozle import translate as compatibility
from bamboozle.agent_files import AgentFiles
from bamboozle.agent_policy import AgentPolicy
from bamboozle.dictionary import Dictionary
from bamboozle.formats import DocumentCodec, FormatRules, format_options
from bamboozle.project_views import export_view
from bamboozle.restoration import prepare_restore, verify_restoration
from bamboozle.return_validation import inspect_return
from bamboozle.service import OperationError, ledger_path, prepare, recover, verify
from bamboozle.utils import atomic_write, json_bytes
from bamboozle.workspace import Workspace

MAPPING = Dictionary({"alpha": "omega", "task": "stage"})
RULES = FormatRules("text", protect=("paths", "urls"))


@pytest.mark.parametrize(
    "literal",
    [
        r"C:\models\alpha.bin",
        "D:/alpha/omega.json",
        r"\\server\alpha\omega.bin",
        "/data/alpha",
        "./alpha",
        "../alpha/omega",
        "~/alpha/file",
        r"~\alpha\omega",
        "assets/alpha.csv",
        "alpha.json",
        "café/alpha.json",
        '"C:\\my alpha\\omega.bin"',
        "'../alpha folder/omega.bin'",
        "`alpha folder/omega.bin`",
        "https://alpha.test/api/omega?q=alpha#omega",
        "https://alpha.test/item_(omega)",
        "mailto:alpha@example.test",
        "www.alpha.test/omega",
        '"https://alpha.test/omega"',
        r"C:\__SCM_LIT_omega__\__SCM_CASE_broken__.bin",
    ],
)
def test_exact_literals_and_context_survive_surrounding_prose(literal):
    text = f"\ufeffalpha omega ({literal}), task.\r\n"
    codec = DocumentCodec(MAPPING, RULES)
    encoded = codec.transform(text)
    assert literal in encoded.text
    assert encoded.text.startswith("\ufeffomega __SCM_LIT_omega__ (")
    assert encoded.text.endswith(", stage.\r\n")
    assert (
        codec.transform(encoded.text, "reverse", context=encoded.context).text == text
    )
    assert encoded.context["spans"]
    assert codec.transform(
        encoded.text + "new stage\n", "reverse", context=encoded.context
    ).text.endswith("new task\n")


def test_explicit_selection_and_historical_text_behavior():
    original = "alpha https://alpha.test/alpha C:/alpha.bin"
    assert (
        DocumentCodec(MAPPING).transform(original).text
        == "omega https://omega.test/omega C:/omega.bin"
    )
    url = DocumentCodec(MAPPING, FormatRules("text", protect=("urls",)))
    assert url.transform(original).text == "omega https://alpha.test/alpha C:/omega.bin"
    path = DocumentCodec(MAPPING, FormatRules("text", protect=("paths",)))
    assert (
        path.transform(original).text == "omega https://omega.test/omega C:/alpha.bin"
    )
    assert FormatRules().document() == {"format": "text"}
    assert FormatRules.from_document(RULES.document()) == RULES
    assert format_options("text", None, protect=["paths", "urls"]) == RULES
    with pytest.raises(ValueError, match="requires"):
        format_options(None, None, protect=["paths"])


@pytest.mark.parametrize(
    "rule",
    [
        {"format": "json", "pointers": ["/x"], "protect": ["paths"]},
        {"format": "text", "protect": []},
        {"format": "text", "protect": "paths"},
        {"format": "text", "protect": ["paths", "paths"]},
        {"format": "text", "protect": ["unknown"]},
        {"format": "text", "protect": [True]},
    ],
)
def test_bad_rules_refuse_before_processing(rule):
    with pytest.raises((ValueError, TypeError)):
        FormatRules.from_document(rule)


def test_marker_protocol_is_atomic_and_damaged_prose_markers_are_not_hidden():
    codec = DocumentCodec(
        Dictionary({"alpha": "https://omega.test/path"}),
        FormatRules("text", protect=("paths",)),
    )
    raw = "native https://omega.test/path plus __SCM_LIT_example__"
    result = codec.transform(raw)
    assert codec.transform(result.text, "reverse", context=result.context).text == raw
    codec = DocumentCodec(MAPPING, RULES)
    encoded = codec.transform("alpha __SCM_LIT_omega__ C:/__SCM_CASE_invalid__.bin")
    assert (
        codec.transform(encoded.text, "reverse", context=encoded.context).text
        == "alpha __SCM_LIT_omega__ C:/__SCM_CASE_invalid__.bin"
    )
    with pytest.raises(ValueError, match="marker"):
        codec.transform("__SCM_LIT_https://omega.test/bad__", "reverse")


def test_dictionary_created_literal_shape_is_refused_instead_of_guessing():
    with pytest.raises(ValueError, match="retained|syntax"):
        DocumentCodec(Dictionary({"alpha": "omega.bin"}), RULES).transform("alpha")


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", -1),
        ("encoded", True),
        ("encoded", 99999),
        ("kind", "urls"),
        ("value", "not a path"),
    ],
)
def test_damaged_private_span_context_refused(field, value):
    codec = DocumentCodec(MAPPING, RULES)
    encoded = codec.transform("alpha C:/alpha.bin")
    context = copy.deepcopy(encoded.context)
    context["spans"][0][field] = value
    with pytest.raises(ValueError):
        codec.transform(encoded.text, "reverse", context=context)


def test_bounds_and_punctuation(monkeypatch):
    codec = DocumentCodec(MAPPING, RULES)
    sample = "alpha (https://alpha.test/item_(omega)), alpha! 1.25"
    encoded = codec.transform(sample)
    assert encoded.text == "omega (https://alpha.test/item_(omega)), omega! 1.25"
    monkeypatch.setattr(text_literals, "MAX_SPANS", 2)
    with pytest.raises(ValueError, match="literal limit"):
        codec.transform("a.txt b.txt c.txt")
    with pytest.raises(ValueError, match="size limit"):
        DocumentCodec(MAPPING, RULES, limit=10).transform("C:/alpha/too-long.txt")


def test_review_reports_changed_and_reordered_literals_without_term_false_positives():
    codec = DocumentCodec(MAPPING, RULES)
    baseline = codec.transform("alpha C:/alpha.bin https://alpha.test/task")
    unchanged = inspect_return(
        baseline.text.encode(),
        MAPPING,
        baseline=baseline.text.encode(),
        rules=RULES,
        context=baseline.context,
    )
    assert unchanged["status"] == "accepted"
    changed = inspect_return(
        baseline.text.replace("C:/alpha.bin", "C:/omega.bin").encode(),
        MAPPING,
        baseline=baseline.text.encode(),
        rules=RULES,
        context=baseline.context,
    )
    assert changed["counts"] == {"protected_literal_changed": 2}
    assert changed["status"] == "needs_review" and changed["decodable"]
    reordered = inspect_return(
        b"omega https://alpha.test/task C:/alpha.bin",
        MAPPING,
        baseline=baseline.text.encode(),
        rules=RULES,
        context=baseline.context,
    )
    assert reordered["counts"] == {"protected_literals_reordered": 1}


def test_manual_ledger_recovery_reviewed_restoration_and_exact_old_rules(tmp_path):
    workspace = Workspace(tmp_path)
    original = b"alpha C:/alpha.bin https://alpha.test/task\r\n"
    (tmp_path / "source.txt").write_bytes(original)
    prepare(
        workspace,
        "source.txt",
        output="encoded.txt",
        dictionary=MAPPING,
        format_rules=RULES,
    ).apply()
    assert verify(workspace, "encoded.txt")["status"] == "verified"
    metadata = ledger_path(tmp_path / "encoded.txt")
    saved = json.loads(metadata.read_bytes())
    assert saved["document"]["rules"] == RULES.document()
    metadata.unlink()
    assert recover(
        workspace, "encoded.txt", "source.txt", MAPPING, apply=True, format_rules=RULES
    )["ledger_saved"]
    encoded = (tmp_path / "encoded.txt").read_bytes()
    (tmp_path / "edited.txt").write_bytes(encoded + b"new stage ./omega.bin\r\n")
    plan = prepare_restore(
        workspace,
        "edited.txt",
        "source.txt",
        output="restored.txt",
        ledger=metadata,
        terms="translate",
    )
    plan.apply(plan.review_sha256)
    assert (
        tmp_path / "restored.txt"
    ).read_bytes() == original + b"new task ./omega.bin\r\n"
    assert verify_restoration(workspace, "restored.txt")["status"] == "verified"
    with pytest.raises(ValueError, match="saved|different|match"):
        prepare(workspace, "encoded.txt", mode="reverse", format_rules=FormatRules())


def test_source_agent_reads_edits_reconnects_and_new_files_share_protection(tmp_path):
    workspace = Workspace(tmp_path)
    (tmp_path / "settings.txt").write_text("alpha C:/alpha.bin")
    policy = {
        "schema_version": 2,
        "files": {
            "settings.txt": {"policy": "translate", "document": RULES.document()}
        },
        "new_files": {"new": {"policy": "translate", "document": RULES.document()}},
    }
    (tmp_path / "config.agent-policy.json").write_bytes(json_bytes(policy))
    service = AgentFiles(
        workspace, MAPPING, policy=AgentPolicy(workspace, "config.agent-policy.json")
    )
    read = service.read("settings.txt")
    assert (
        read["content"] == "omega C:/alpha.bin"
        and read["document_rules"] == RULES.document()
    )
    second = AgentFiles(
        workspace, MAPPING, policy=AgentPolicy(workspace, "config.agent-policy.json")
    )
    second.write(
        "settings.txt",
        read["content"] + " stage",
        expected_sha256=read["source_sha256"],
        expected_context_sha256=read["context_sha256"],
    )
    assert (tmp_path / "settings.txt").read_text() == "alpha C:/alpha.bin task"
    second.write("new/generated.txt", "omega https://alpha.test/stage C:/omega.bin")
    assert (
        tmp_path / "new/generated.txt"
    ).read_text() == "alpha https://alpha.test/stage C:/omega.bin"


def test_partial_project_import_recovers_saved_protected_context(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path)
    (tmp_path / "a.txt").write_text("alpha C:/alpha.bin")
    (tmp_path / "b.txt").write_text("alpha https://alpha.test/task")
    policy = {
        "schema_version": 2,
        "files": {
            name: {"policy": "translate", "document": RULES.document()}
            for name in ("a.txt", "b.txt")
        },
        "new_files": {},
    }
    (tmp_path / "policy.json").write_bytes(json_bytes(policy))
    exported = export_view(workspace, "policy.json", "view", MAPPING)
    for name in ("a.txt", "b.txt"):
        path = tmp_path / "view" / name
        path.write_text(path.read_text() + " stage")
    review = view_import.review_import(workspace, exported["view_id"])
    assert review["ok"]

    def fail(path, data, **kwargs):
        if path == tmp_path / "b.txt":
            raise OSError("injected second-file interruption")
        atomic_write(path, data, **kwargs)

    monkeypatch.setattr(view_import, "atomic_write", fail)
    with pytest.raises(OperationError):
        view_import.apply_import(
            workspace, exported["view_id"], review["review_sha256"]
        )
    monkeypatch.setattr(view_import, "atomic_write", atomic_write)
    view_import.apply_import(
        workspace, exported["view_id"], review["review_sha256"], recover=True
    )
    assert (tmp_path / "a.txt").read_text() == "alpha C:/alpha.bin task"
    assert (tmp_path / "b.txt").read_text() == "alpha https://alpha.test/task task"


def test_complete_literal_application_workflows(tmp_path):
    run = runpy.run_path(str(Path(__file__).parents[3] / "scripts/literals_smoke.py"))[
        "run"
    ]
    result = run(tmp_path / "applications")
    assert result["application_cases"] == 160
    assert set(result["workflows"]) == {"cli", "mcp", "agent"}


def test_compatibility_file_workflow_and_stream_context_refusal(tmp_path, capsys):
    (tmp_path / "mapping.json").write_bytes(json_bytes(MAPPING.document()))
    original = b"alpha ./alpha.bin https://alpha.test/task\r\n"
    (tmp_path / "source.txt").write_bytes(original)
    common = ["--root", str(tmp_path), "--dict", "mapping.json"]
    assert (
        compatibility.main(
            [
                *common,
                "--mode",
                "forward",
                "--input",
                "source.txt",
                "--output",
                "neutral.txt",
                "--format",
                "text",
                "--protect",
                "paths",
                "--protect",
                "urls",
            ]
        )
        == 0
    )
    assert (
        compatibility.main(
            [
                "--root",
                str(tmp_path),
                "--mode",
                "reverse",
                "--input",
                "neutral.txt",
                "--output",
                "restored.txt",
                "--strict",
            ]
        )
        == 0
    )
    assert (tmp_path / "restored.txt").read_bytes() == original
    assert (
        compatibility.main(
            [
                *common,
                "--mode",
                "forward",
                "--input",
                "source.txt",
                "--output",
                "-",
                "--format",
                "text",
                "--protect",
                "paths",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert not captured.out and "file-to-file" in captured.err


@pytest.mark.parametrize("key", ["source_chars", "encoded_chars"])
def test_retained_length_tampering_cannot_pass_unchanged_roundtrip(key):
    codec = DocumentCodec(MAPPING, RULES)
    encoded = codec.transform("alpha ./alpha.bin")
    context = copy.deepcopy(encoded.context)
    context[key] += 1
    with pytest.raises(ValueError, match="retained exact"):
        codec.transform(encoded.text, "reverse", context=context)


def test_quoted_prose_is_translated_and_literals_do_not_cross_newlines():
    codec = DocumentCodec(MAPPING, RULES)
    raw = '"alpha task" \'https://alpha.test/task\'\n"./alpha\ntask"'
    encoded = codec.transform(raw)
    assert encoded.text == '"omega stage" \'https://alpha.test/task\'\n"./alpha\nstage"'
    assert codec.transform(encoded.text, "reverse", context=encoded.context).text == raw
