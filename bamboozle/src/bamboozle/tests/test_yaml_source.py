"""YAML translation, application-facing publication and protected ownership."""

import asyncio
import json
import runpy
from pathlib import Path

import pytest
import yaml
from fastmcp import Client

from bamboozle.dictionary import Dictionary
from bamboozle.formats import DocumentCodec, FormatRules, format_options
from bamboozle.mcp import create_server
from bamboozle.return_validation import validate_return
from bamboozle.service import ledger_path, prepare, recover, verify
from bamboozle.workspace import Workspace
from bamboozle.yaml_source import YamlDocument

MAPPING = Dictionary({"alpha": "omega", "high priority": "fast lane"})
RULES = FormatRules("yaml", ("/message",))


@pytest.mark.parametrize(
    "scalar",
    [
        "alpha",
        "'alpha'",
        '"\\u0061lpha"',
        "!!str alpha",
        "! alpha",
        "|\n  alpha\n",
        "|- # alpha comment\n  alpha\n",
        "|+\n  alpha\n\n",
        ">-\n  high\n  priority alpha\n",
        '"al\\\n  pha"',
        "'alpha''s high priority'",
        "|2-\n  alpha\n",
    ],
)
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_scalar_styles_and_exact_saved_spelling(scalar, ending):
    source = (
        "\ufeff%YAML 1.2\n---\n# alpha\nmessage: "
        + scalar
        + "\nid: alpha\nflag: true\n..."
    ).replace("\n", ending)
    codec = DocumentCodec(MAPPING, RULES)
    result = codec.transform(source)
    assert "# alpha" in result.text and "id: alpha" in result.text
    assert "flag: true" in result.text
    original, encoded = yaml.safe_load(source), yaml.safe_load(result.text)
    assert encoded["message"] == codec.translator.forward(original["message"])
    assert (
        codec.transform(result.text, "reverse", context=result.context).text == source
    )
    assert json.loads(json.dumps(result.context)) == result.context


@pytest.mark.parametrize(
    "source,pointer",
    [
        ("'alpha'", ""),
        ("a/b:\n  '~key': alpha\n", "/a~1b/~0key"),
        ("items: [alpha, 'alpha', 42]\n", "/items/1"),
        ("a:\n  - |2- # keep\n    alpha\n", "/a/0"),
        ("a:\n  b: |4+ # keep\n      alpha\n\n", "/a/b"),
        ("{message: alpha, id: alpha}", "/message"),
    ],
)
def test_nested_flow_root_and_explicit_block_indent(source, pointer):
    codec = DocumentCodec(MAPPING, FormatRules("yaml", (pointer,)))
    encoded = codec.transform(source)
    assert encoded.substitutions == 1
    assert (
        codec.transform(encoded.text, "reverse", context=encoded.context).text == source
    )


@pytest.mark.parametrize(
    "target",
    [
        "true",
        "null",
        "0x42",
        "3.0",
        "[a, b]",
        "a: b",
        "a # b",
        "x'quote",
        'x"quote',
        "!custom",
        "*alias",
        "hello\u2028world",
        "control\x01here",
        "del\x7fhere",
        "c1\x80here",
    ],
)
@pytest.mark.parametrize("style", ["alpha", "'alpha'", '"alpha"', "|-\n  alpha\n"])
def test_replacements_cannot_change_scalar_type_or_structure(target, style):
    codec = DocumentCodec(Dictionary({"alpha": target}), RULES)
    source = "message: " + style + "\nid: untouched\n"
    result = codec.transform(source)
    assert YamlDocument(result.text).paths["/message"].value == target
    assert YamlDocument(result.text).paths["/message"].kind == "string"
    assert (
        codec.transform(result.text, "reverse", context=result.context).text == source
    )


def test_alias_graph_requires_all_value_owners_and_changes_one_physical_scalar():
    source = (
        "base: &base {message: &word 'alpha', id: alpha}\ncopy: *base\nmessage: *word\n"
    )
    pointers = ("/base/message", "/copy/message", "/message")
    codec = DocumentCodec(MAPPING, FormatRules("yaml", pointers))
    result = codec.transform(source)
    assert result.text == source.replace("'alpha'", "'omega'")
    assert result.substitutions == 1
    assert len(codec.regions(result.text)) == 1
    assert set(result.context) == {"/base/message"}
    assert (
        codec.transform(result.text, "reverse", context=result.context).text == source
    )
    for selection in (pointers[:1], pointers[1:]):
        with pytest.raises(ValueError, match="alias"):
            DocumentCodec(MAPPING, FormatRules("yaml", selection)).transform(source)


def test_unselected_custom_tags_merges_anchors_and_aliases_remain_exact():
    source = "%TAG !e! tag:example.com,2026:\n---\nbase: &base {id: alpha}\nconfig: {<<: *base, number: 3}\nopaque: !e!secret alpha\nmessage: alpha\n"
    codec = DocumentCodec(MAPPING, RULES)
    assert codec.transform(source).text == source.replace(
        "message: alpha", "message: omega"
    )
    for pointer in ("/base/id", "/config/<</id", "/opaque"):
        with pytest.raises(ValueError):
            DocumentCodec(MAPPING, FormatRules("yaml", (pointer,))).transform(source)


@pytest.mark.parametrize(
    "source",
    [
        "message: alpha\nmessage: beta\n",
        "message: [broken",
        "message: 7",
        "message: null",
        "id: alpha",
        "message: *undefined",
        "message: &a alpha\nid: &a beta",
        "%YAML 1.1\n---\nmessage: alpha",
        "message: alpha\n---\nmessage: alpha",
        "true: alpha\nmessage: alpha",
        "? [a,b]\n: value\nmessage: alpha",
        "message: &a [*a]",
        "message: !!int alpha",
        "message: !custom alpha",
        "key: &key message\n*key: alpha\nmessage: alpha",
    ],
)
def test_ambiguous_malformed_or_nonstring_yaml_refused_before_output(source, tmp_path):
    (tmp_path / "config.yaml").write_text(source, encoding="utf-8")
    with pytest.raises(ValueError):
        prepare(
            Workspace(tmp_path), "config.yaml", dictionary=MAPPING, format_rules=RULES
        )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.yaml"]


def test_yaml12_core_resolution_without_yaml11_boolean_or_date_inference():
    codec = DocumentCodec(
        Dictionary({"yes": "enabled", "2026-10-04": "today"}),
        FormatRules("yaml", ("/message", "/date")),
    )
    result = codec.transform("message: yes\ndate: 2026-10-04\n")
    assert result.text == "message: enabled\ndate: today\n"
    assert (
        codec.transform(result.text, "reverse", context=result.context).text
        == "message: yes\ndate: 2026-10-04\n"
    )


def test_bounded_aliases_nesting_and_file_limits():
    with pytest.raises(ValueError, match="limit"):
        DocumentCodec(MAPPING, RULES, limit=20).transform("message: omega omega")
    with pytest.raises(ValueError, match="nesting"):
        YamlDocument("[" * 66 + "alpha" + "]" * 66)
    with pytest.raises(ValueError, match="limit"):
        YamlDocument("message: " + "x" * 1000000)
    bomb = "a: &a [alpha, alpha]\n"
    for index in range(18):
        prior = "a" if index == 0 else f"a{index - 1}"
        bomb += f"a{index}: &a{index} [*{prior}, *{prior}]\n"
    with pytest.raises(ValueError, match="expansion"):
        YamlDocument(bomb)


def test_saved_context_cannot_insert_other_yaml_nodes_or_reinterpret_mapping():
    codec = DocumentCodec(MAPPING, RULES)
    encoded = codec.transform("message: 'alpha'\n")
    for changed in (
        {"original": "'alpha'\nother: secret", "encoded": "'omega'", "indent": 0},
        {"original": "'secret'", "encoded": "'omega'", "indent": 0},
        {"original": "'alpha'", "encoded": "'omega'", "indent": True},
        {"original": "'alpha'", "encoded": "'omega'", "indent": 0, "extra": True},
    ):
        with pytest.raises(ValueError):
            codec.transform(encoded.text, "reverse", context={"/message": changed})


def test_ledger_recovery_edited_return_and_protected_diagnostics(tmp_path):
    workspace = Workspace(tmp_path)
    original = b'\xef\xbb\xbfmessage: "\\u0061lpha"\r\nid: alpha # unchanged\r\n'
    (tmp_path / "config.yaml").write_bytes(original)
    prepare(workspace, "config.yaml", dictionary=MAPPING, format_rules=RULES).apply()
    assert verify(workspace, "config_safe.yaml")["status"] == "verified"
    ledger_path(tmp_path / "config_safe.yaml").unlink()
    assert recover(
        workspace,
        "config_safe.yaml",
        "config.yaml",
        MAPPING,
        format_rules=RULES,
        apply=True,
    )["ledger_saved"]
    returned = tmp_path / "config_safe.yaml"
    returned.write_bytes(
        returned.read_bytes()
        .replace(b'"omega"', b'"omega improved"')
        .replace(b"# unchanged", b"# edited")
    )
    report = validate_return(workspace, "config_safe.yaml", "config.yaml")
    assert "protected_yaml_changed" in json.dumps(report)
    prepare(workspace, "config_safe.yaml", mode="reverse").apply()
    assert yaml.safe_load((tmp_path / "config_safe_ready.yaml").read_bytes()) == {
        "message": "alpha improved",
        "id": "alpha",
    }


def test_operator_mcp_yaml_rules_survive_ledger_roundtrip(tmp_path):
    original = b"message: &word 'alpha'\ncopy: *word\nid: alpha\n"
    (tmp_path / "config.yaml").write_bytes(original)

    async def exercise():
        async with Client(create_server(tmp_path, MAPPING)) as client:

            async def call(name, args):
                result = await client.call_tool(name, args)
                assert not result.is_error and result.content[0].type == "text"
                return json.loads(result.content[0].text)

            preview = await call(
                "preview_file",
                {
                    "source": "config.yaml",
                    "document_format": "yaml",
                    "yaml_pointers": ["/message", "/copy"],
                },
            )
            await call("apply_preview", {"plan_id": preview["plan_id"]})
            preview = await call(
                "preview_file",
                {"source": "config_safe.yaml", "reverse": True, "strict": True},
            )
            await call("apply_preview", {"plan_id": preview["plan_id"]})

    asyncio.run(exercise())
    assert (tmp_path / "config_safe_ready.yaml").read_bytes() == original


def test_yaml_rule_schema_and_cli_option_ownership():
    assert FormatRules.from_document(RULES.document()) == RULES
    assert format_options("yaml", None, yaml_pointers=["/message"]) == RULES
    with pytest.raises(ValueError, match="requires"):
        format_options("yaml", ["/message"])
    with pytest.raises(ValueError, match="requires"):
        format_options("json", None, yaml_pointers=["/message"])


def test_parser_errors_report_locations_without_source_vocabulary():
    with pytest.raises(ValueError, match="line") as error:
        DocumentCodec(MAPPING, RULES).transform(
            "message: [private-source-word, broken: ["
        )
    assert "private-source-word" not in str(error.value)


def test_escaped_markers_on_return_are_decoded_as_values_not_yaml_spelling():
    codec = DocumentCodec(MAPPING, RULES)
    assert (
        codec.transform('message: "__SCM_LIT_om\\u0065ga__"', "reverse").text
        == 'message: "omega"'
    )
    for text in ("message: !!str", "message: !!str\n", "message: ''"):
        assert codec.transform(text).text == text


def test_folded_phrase_reflow_keeps_explicit_header_and_comment_spacing():
    source = "message: >2-  #  alpha stays here\r\n  high\r\n  priority alpha\r\n"
    result = DocumentCodec(MAPPING, RULES).transform(source)
    assert result.text.startswith("message: >2-  #  alpha stays here\r\n")
    assert yaml.safe_load(result.text)["message"] == "fast lane omega"


@pytest.mark.parametrize("target", ["tab\there", "control\x01here", "bell\x07here"])
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_folded_phrase_can_be_quoted_without_losing_comments_or_context(target, ending):
    source = (
        "message: >-  # preserve alpha\n  high\n  priority\nid: unchanged\n".replace(
            "\n", ending
        )
    )
    codec = DocumentCodec(Dictionary({"high priority": target}), RULES)
    result = codec.transform(source)
    assert yaml.safe_load(result.text)["message"] == target
    assert "  # preserve alpha" + ending + "id: unchanged" in result.text
    assert (
        codec.transform(result.text, "reverse", context=result.context).text == source
    )


def test_agent_added_alias_cannot_silently_change_an_unselected_value(tmp_path):
    from bamboozle.agent_files import AgentFiles
    from bamboozle.agent_policy import AgentPolicy
    from bamboozle.utils import json_bytes

    policy_path = "source.agent-policy.json"
    (tmp_path / policy_path).write_bytes(
        json_bytes(
            {
                "schema_version": 2,
                "files": {
                    "config.yaml": {"policy": "translate", "document": RULES.document()}
                },
                "new_files": {},
            }
        )
    )
    original = b"message: &message alpha\nid: unchanged\n"
    (tmp_path / "config.yaml").write_bytes(original)
    workspace = Workspace(tmp_path)
    files = AgentFiles(workspace, MAPPING, policy=AgentPolicy(workspace, policy_path))
    read = files.read("config.yaml")
    with pytest.raises(ValueError, match="alias"):
        files.write(
            "config.yaml",
            read["content"] + "copy: *message\n",
            read["source_sha256"],
            read["context_sha256"],
        )
    assert (tmp_path / "config.yaml").read_bytes() == original


def test_complete_yaml_application_cli_stdio_views_and_recovery(tmp_path):
    script = Path(__file__).parents[3] / "scripts/yaml_smoke.py"
    result = runpy.run_path(str(script))["run"](tmp_path / "yaml-application")
    assert result["status"] == "passed"
    assert result["agent"]["check"]["status"] == "passed"
    assert result["recovered_import"]["receipt_saved"]
