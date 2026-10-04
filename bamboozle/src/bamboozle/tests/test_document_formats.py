"""Format adapters must preserve executable structure through real operator workflows."""

import asyncio
import json
import re
import subprocess
import sys

import pytest
from fastmcp import Client

from bamboozle import view_import
from bamboozle.cipher import CipherEngine
from bamboozle.cli import main
from bamboozle.dictionary import Dictionary
from bamboozle.formats import DocumentCodec, FormatRules, format_options
from bamboozle.mcp import create_server
from bamboozle.project_views import export_view, load_view
from bamboozle.restoration import prepare_restore, verify_restoration
from bamboozle.service import OperationError, ledger_path, prepare, recover, verify
from bamboozle.tessera import seal, unseal
from bamboozle.utils import json_bytes
from bamboozle.view_import import apply_import, review_import
from bamboozle.workspace import Workspace

MAPPING = Dictionary({"alpha": "omega"})
MARKDOWN = FormatRules("markdown")
JSON = FormatRules("json", ("/prompt",))


@pytest.mark.parametrize(
    "protected",
    [
        "`alpha omega`",
        "``alpha ` omega``",
        "`alpha\nomega`",
        '[alpha](https://example.com/alpha "alpha")',
        "![alpha](alpha.png)",
        '[alpha][ref]\n\n[ref]: /alpha/path "alpha"\n',
        "<https://example.com/alpha>",
        "alpha@example.com",
        "https://example.com/alpha",
        "C:\\alpha\\omega.py",
        "../alpha/file",
        "/alpha/file",
        "alpha.json",
        "```python\nalpha = 'omega'\n```\n",
        "~~~alpha\nalpha omega\n~~~\n",
        "    alpha omega\n",
        "> ```alpha\n> alpha omega\n> ```\n",
        "<div>\nalpha omega\n</div>\n",
        "<!-- alpha omega -->\n",
    ],
)
def test_markdown_protects_technical_spans_without_losing_surrounding_prose(protected):
    source = "alpha omega\n\n" + protected + "\n\nalpha.\n"
    codec = DocumentCodec(MAPPING, MARKDOWN)
    encoded = codec.transform(source)
    assert encoded.text.startswith("omega __SCM_LIT_omega__\n")
    assert protected in encoded.text
    assert encoded.text.endswith("omega.\n")
    assert codec.transform(encoded.text, "reverse").text == source


def test_markdown_bom_crlf_unicode_and_executable_fences():
    source = "\ufeff# Alpha\r\n\r\nalpha café\r\n\r\n```python\r\nalpha = 7\r\nassert alpha == 7\r\n```\r\n"
    engine = CipherEngine(MAPPING)
    encoded = engine.encode_document(source, MARKDOWN)
    assert encoded.text.startswith("\ufeff# Omega\r\n")
    match = re.search(r"```python\r\n(.*?)```", encoded.text, re.DOTALL)
    assert match is not None
    code = match.group(1)
    subprocess.run([sys.executable, "-c", code], check=True, timeout=10)
    assert engine.decode_document(encoded.text, MARKDOWN) == source


def test_markdown_incomplete_fence_and_marker_examples_remain_literal():
    codec = DocumentCodec(MAPPING, MARKDOWN)
    source = "alpha\n\n```\n__SCM_CASE_broken__ alpha omega\n"
    assert codec.transform(source).text == source.replace("alpha\n", "omega\n", 1)
    assert (
        codec.transform("alpha ![alpha `omega`](x.png) alpha").text
        == "omega ![alpha `omega`](x.png) omega"
    )


def test_markdown_inline_code_cannot_cross_paragraph_boundaries():
    codec = DocumentCodec(MAPPING, MARKDOWN)
    assert codec.transform("`alpha\n\nalpha`\n").text == "`omega\n\nomega`\n"
    assert codec.transform("- `alpha`\n- alpha\n").text == "- `alpha`\n- omega\n"


@pytest.mark.parametrize("markup", ["_", "__", "*", "**", "***", "___"])
def test_markdown_emphasis_translates_prose_without_splitting_cipher_markers(markup):
    codec = DocumentCodec(MAPPING, MARKDOWN)
    source = f"{markup}alpha omega{markup} alpha_beta\n"
    encoded = codec.transform(source)
    assert encoded.text == f"{markup}omega __SCM_LIT_omega__{markup} alpha_beta\n"
    assert codec.transform(encoded.text, "reverse").text == source


def test_markdown_mixed_case_markers_and_invalid_prose_markers():
    codec = DocumentCodec(MAPPING, MARKDOWN)
    source = "__aLpHa__ alpha_beta alpha omega\n"
    encoded = codec.transform(source)
    assert "__SCM_CASE_" in encoded.text
    assert codec.transform(encoded.text, "reverse").text == source
    with pytest.raises(ValueError):
        codec.transform("__SCM_CASE_broken__", "reverse")


def test_markdown_long_non_address_runs_finish_without_quadratic_rescanning():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from bamboozle.formats import DocumentCodec, FormatRules; from bamboozle.dictionary import Dictionary; codec=DocumentCodec(Dictionary({'alpha':'omega'}), FormatRules('markdown')); text=('a'*100000)+'\\n'+('-'*100000)+'\\nalpha'; result=codec.transform(text); assert result.text.endswith('omega')",
        ],
        check=True,
        timeout=15,
    )


def test_selected_json_values_preserve_exact_original_escapes_and_other_tokens():
    source = '\ufeff{\r\n  "alpha": 1e+09, "prompt": "\\u0061lpha / omega",\r\n  "id":"alpha", "items":["alpha",3], "a/b":{"~key":"Alpha"}\r\n}\r\n'
    rules = FormatRules("json", ("/prompt", "/items/0", "/a~1b/~0key"))
    codec = DocumentCodec(MAPPING, rules)
    encoded = codec.transform(source)
    assert '"alpha": 1e+09' in encoded.text and '"id":"alpha"' in encoded.text
    assert (
        json.loads(encoded.text.lstrip("\ufeff"))["prompt"]
        == "omega / __SCM_LIT_omega__"
    )
    assert (
        codec.transform(encoded.text, "reverse", context=encoded.context).text == source
    )
    edited = encoded.text.replace('"id":"alpha"', '"id":"omega"')
    assert codec.transform(
        edited, "reverse", context=encoded.context
    ).text == source.replace('"id":"alpha"', '"id":"omega"')
    edited = encoded.text.replace(
        "omega / __SCM_LIT_omega__", "omega / __SCM_LIT_omega__ edited"
    )
    decoded = codec.transform(edited, "reverse", context=encoded.context).text
    assert json.loads(decoded.lstrip("\ufeff"))["prompt"] == "alpha / omega edited"
    assert '"alpha": 1e+09' in decoded


def test_root_json_string_and_new_content_need_no_original_counterpart():
    codec = DocumentCodec(MAPPING, FormatRules("json", ("",)))
    encoded = codec.transform(' "\\u0061lpha"\r\n')
    assert (
        codec.transform(encoded.text, "reverse", context=encoded.context).text
        == ' "\\u0061lpha"\r\n'
    )
    assert (
        DocumentCodec(MAPPING, JSON)
        .transform('{"id":"omega","prompt":"omega"}', "reverse")
        .text
        == '{"id":"omega","prompt":"alpha"}'
    )


@pytest.mark.parametrize(
    "document",
    [
        '{"prompt": 7}',
        '{"prompt":null}',
        '{"id":"alpha"}',
        '{"prompt":"alpha","prompt":"omega"}',
        '{"prompt":NaN}',
        '{"prompt":"alpha",}',
        '{"prompt":"unterminated}',
    ],
)
def test_invalid_json_and_nonstring_or_missing_selection_fail(document):
    with pytest.raises(ValueError):
        DocumentCodec(MAPPING, JSON).transform(document)


@pytest.mark.parametrize(
    "document",
    [
        {"format": "json", "pointers": []},
        {"format": "json", "pointers": ["prompt"]},
        {"format": "json", "pointers": ["/~2"]},
        {"format": "json", "pointers": ["/a", "/a"]},
        {"format": "json", "pointers": [3]},
        {"format": "json", "pointers": "/prompt"},
        {"format": "markdown", "pointers": ["/a"]},
        {"format": "yaml"},
        {"format": "text", "unknown": True},
    ],
)
def test_invalid_format_rules_fail(document):
    with pytest.raises((ValueError, TypeError)):
        FormatRules.from_document(document)


def test_context_rejects_mapping_mismatch_and_unselected_originals():
    codec = DocumentCodec(MAPPING, JSON)
    for context in (
        {"/id": {"original": '"alpha"', "encoded": '"omega"'}},
        {"/prompt": {"original": '"secret"', "encoded": '"omega"'}},
        {"/prompt": {"original": "7", "encoded": '"omega"'}},
        {"/prompt": {"original": '"alpha"', "encoded": '"omega"', "extra": True}},
    ):
        with pytest.raises((ValueError, TypeError)):
            codec.transform('{"prompt":"omega"}', "reverse", context=context)


def test_document_limits_reject_expansion_nesting_and_unsupported_options():
    with pytest.raises(ValueError, match="limit"):
        DocumentCodec(MAPPING, JSON, limit=25).transform('{"prompt":"omega omega"}')
    with pytest.raises(ValueError, match="nesting"):
        DocumentCodec(MAPPING, JSON).transform("[" * 66 + '"alpha"' + "]" * 66)
    with pytest.raises(ValueError, match="requires"):
        format_options(None, ["/prompt"])


def test_ledger_keeps_format_snapshot_and_strict_bytes(tmp_path):
    workspace = Workspace(tmp_path)
    original = b'\xef\xbb\xbf{ "prompt": "\\u0061lpha", "id":"alpha" }\r\n'
    (tmp_path / "template.json").write_bytes(original)
    prepare(workspace, "template.json", dictionary=MAPPING, format_rules=JSON).apply()
    record = json.loads(ledger_path(tmp_path / "template_safe.json").read_bytes())
    assert (
        record["schema_version"] == 3
        and record["marker_version"] == 2
        and record["document"]["rules"] == JSON.document()
    )
    assert verify(workspace, "template_safe.json")["status"] == "verified"
    prepare(workspace, "template_safe.json", mode="reverse", strict=True).apply()
    assert (tmp_path / "template_safe_ready.json").read_bytes() == original
    with pytest.raises(ValueError, match="format rules"):
        prepare(
            workspace,
            "template_safe.json",
            mode="reverse",
            output="wrong.json",
            format_rules=FormatRules(),
        )
    with pytest.raises(ValueError, match="dictionary"):
        prepare(
            workspace,
            "template_safe.json",
            mode="reverse",
            output="wrong.json",
            dictionary=Dictionary({"beta": "delta"}),
        )


def test_structured_missing_ledger_recovery_and_reviewed_restoration(tmp_path):
    workspace = Workspace(tmp_path)
    (tmp_path / "template.json").write_bytes(b'{"prompt":"alpha","id":"alpha"}\r\n')
    prepare(workspace, "template.json", dictionary=MAPPING, format_rules=JSON).apply()
    ledger_path(tmp_path / "template_safe.json").unlink()
    assert recover(
        workspace,
        "template_safe.json",
        "template.json",
        MAPPING,
        format_rules=JSON,
        apply=True,
    )["ledger_saved"]
    edited = tmp_path / "template_safe.json"
    edited.write_bytes(edited.read_bytes().replace(b'"omega"', b'"omega edited"'))
    with pytest.raises(ValueError, match="Structured restoration"):
        prepare_restore(workspace, "template_safe.json", "template.json")
    plan = prepare_restore(
        workspace, "template_safe.json", "template.json", terms="translate"
    )
    plan.apply(plan.review_sha256)
    assert (
        verify_restoration(workspace, "template_safe_ready.json")["status"]
        == "verified"
    )
    assert json.loads((tmp_path / "template_safe_ready.json").read_bytes()) == {
        "prompt": "alpha edited",
        "id": "alpha",
    }


def test_cli_format_selection_and_auto_saved_decode(tmp_path, capsys):
    (tmp_path / "node.json").write_bytes(b'{"prompt":"fuck", "id":"fuck"}\r\n')
    args = ["--root", str(tmp_path)]
    assert (
        main(
            [
                *args,
                "encode",
                "node.json",
                "--format",
                "json",
                "--json-pointer",
                "/prompt",
                "--profile",
                "profanity",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["document_rules"] == JSON.document()
    assert main([*args, "decode", "node_safe.json", "--strict"]) == 0
    capsys.readouterr()
    assert (tmp_path / "node_safe_ready.json").read_bytes() == (
        tmp_path / "node.json"
    ).read_bytes()


def test_operator_mcp_preserves_json_keys_and_unselected_fields(tmp_path):
    (tmp_path / "template.json").write_bytes(b'{"prompt":"alpha", "id":"omega"}\n')

    async def exercise():
        async with Client(create_server(tmp_path, MAPPING)) as client:

            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                assert not result.is_error and result.content[0].type == "text"
                return json.loads(result.content[0].text)

            encoded = await call(
                "preview_file",
                {
                    "source": "template.json",
                    "document_format": "json",
                    "json_pointers": ["/prompt"],
                },
            )
            await call("apply_preview", {"plan_id": encoded["plan_id"]})
            decoded = await call(
                "preview_file",
                {"source": "template_safe.json", "reverse": True, "strict": True},
            )
            await call("apply_preview", {"plan_id": decoded["plan_id"]})

    asyncio.run(exercise())
    assert (tmp_path / "template_safe_ready.json").read_bytes() == (
        tmp_path / "template.json"
    ).read_bytes()


def test_mixed_project_view_restores_selected_fields_and_new_configs(tmp_path):
    workspace = Workspace(tmp_path)
    markdown = "alpha\n\n```python\nalpha = 7\nassert alpha == 7\n```\n"
    (tmp_path / "README.md").write_text(markdown, encoding="utf-8")
    original = b'{ "prompt": "\\u0061lpha", "id":"alpha" }\r\n'
    (tmp_path / "template.json").write_bytes(original)
    policy = {
        "schema_version": 2,
        "files": {
            "README.md": {"policy": "translate", "document": MARKDOWN.document()},
            "template.json": {"policy": "translate", "document": JSON.document()},
        },
        "new_files": {"configs": {"policy": "translate", "document": JSON.document()}},
    }
    (tmp_path / "policy.json").write_bytes(json_bytes(policy))
    exported = export_view(workspace, "policy.json", "view", MAPPING)
    view = tmp_path / "view"
    assert (view / "README.md").read_text(encoding="utf-8") == markdown.replace(
        "alpha\n", "omega\n", 1
    )
    (view / "README.md").write_text(
        (view / "README.md").read_text(encoding="utf-8") + "omega addition\n",
        encoding="utf-8",
    )
    (view / "configs").mkdir()
    (view / "configs/new.json").write_bytes(
        b'{"prompt":"omega generated","id":"omega"}'
    )
    review = review_import(workspace, exported["view_id"])
    assert review["ok"] and review["action_count"] == 2
    apply_import(workspace, exported["view_id"], review["review_sha256"])
    assert (tmp_path / "template.json").read_bytes() == original
    assert json.loads((tmp_path / "configs/new.json").read_bytes()) == {
        "prompt": "alpha generated",
        "id": "omega",
    }
    assert (
        (tmp_path / "README.md")
        .read_text(encoding="utf-8")
        .endswith("alpha addition\n")
    )
    assert load_view(workspace, exported["view_id"])[1]["policy"] == policy


def test_translation_and_encryption_remain_distinct_composable_operations():
    codec = DocumentCodec(MAPPING, JSON)
    original = '{"id":"alpha","prompt":"alpha"}\r\n'
    encoded = codec.transform(original)
    key = bytes(range(32))
    packet = seal(encoded.text.encode(), key, "local-operator")
    assert packet != encoded.text.encode() and b'"prompt"' not in packet
    returned = unseal(packet, key, "local-operator").decode()
    assert (
        codec.transform(returned, "reverse", context=encoded.context).text == original
    )
    with pytest.raises(ValueError):
        unseal(packet, key, "other-operator")


def test_structured_view_recovery_uses_saved_json_context(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path)
    original = b'{"prompt":"\\u0061lpha", "id":"omega"}\r\n'
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_bytes(original)
    policy = {
        "schema_version": 2,
        "files": {
            name: {"policy": "translate", "document": JSON.document()}
            for name in ("a.json", "b.json")
        },
        "new_files": {},
    }
    (tmp_path / "policy.json").write_bytes(json_bytes(policy))
    exported = export_view(workspace, "policy.json", "view", MAPPING)
    for name in ("a.json", "b.json"):
        path = tmp_path / "view" / name
        path.write_bytes(path.read_bytes().replace(b'"id":"omega"', b'"id":"updated"'))
    reviewed = review_import(workspace, exported["view_id"])
    real_write = view_import.atomic_write

    def fail(path, data, *, overwrite=False):
        if path == tmp_path / "b.json":
            raise OSError("injected structured import failure")
        real_write(path, data, overwrite=overwrite)

    monkeypatch.setattr(view_import, "atomic_write", fail)
    with pytest.raises(OperationError) as error:
        apply_import(workspace, exported["view_id"], reviewed["review_sha256"])
    assert (
        error.value.report["output_saved"] and not error.value.report["receipt_saved"]
    )
    monkeypatch.setattr(view_import, "atomic_write", real_write)
    assert apply_import(
        workspace, exported["view_id"], reviewed["review_sha256"], recover=True
    )["receipt_saved"]
    for name in ("a.json", "b.json"):
        assert (tmp_path / name).read_bytes() == original.replace(
            b'"id":"omega"', b'"id":"updated"'
        )
