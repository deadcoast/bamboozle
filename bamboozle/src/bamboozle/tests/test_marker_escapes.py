"""Prefix quoting, saved protocol boundaries and executable agent-return workflows."""

import asyncio
import json
import random
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

import pytest
from fastmcp import Client

from bamboozle import engine, service, view_import
from bamboozle.agent_files import AgentFiles
from bamboozle.cipher import CipherEngine
from bamboozle.dictionary import CASE_PREFIX, LITERAL_PREFIX, Dictionary
from bamboozle.formats import DocumentCodec, FormatRules
from bamboozle.markers import (
    ESCAPE_PREFIX,
    PREFIX,
    quote_prefix,
    quote_prefixes,
    unquote_prefix,
)
from bamboozle.mcp import create_server
from bamboozle.project_views import CONTRACT, contract, export_view, load_view, put_blob
from bamboozle.restoration import prepare_restore
from bamboozle.return_validation import inspect_return, validate_return
from bamboozle.scm_mcp import create_server as create_agent
from bamboozle.utils import atomic_write, json_bytes, sha256
from bamboozle.workspace import Workspace

MAPPING = Dictionary({"alpha": "beta", "fuck": "assemble"})
RAW = "__SCM_LIT_alpha__ __scm_case_broken __SCM_CASE_1_beta__ __SCM_CASE_x_5f5f53434d5f4c49545f__"


@pytest.mark.parametrize(
    "raw",
    [
        LITERAL_PREFIX,
        CASE_PREFIX,
        "__scm_lit_",
        "__sCm_CaSe_",
        "__SCM_LİT_",
        "__SCM_LIT_beta__",
        "__SCM_LIT_unknown__",
        "__SCM_LIT_alpha beta__",
        "__SCM_CASE_1_beta__",
        "__SCM_CASE_ff_beta__",
        "__SCM_CASE_x_00__",
        "__SCM_CASE__SCM_LIT_",
        "__SCM_LIT___SCM_CASE_",
        "__SCM_CASE___SCM_LIT_",
        "__SCM_LIT_" * 12,
        RAW,
        "\ufeff" + RAW + "\r\nalpha fUcK βeta\r",
    ],
)
def test_marker_like_originals_round_trip_byte_for_byte_without_recursive_decoding(raw):
    cipher = CipherEngine(MAPPING)
    encoded = cipher.transform(raw)
    assert cipher.decode(encoded.text) == raw
    assert encoded.escapes == len(list(PREFIX.finditer(raw)))
    assert cipher.transform(encoded.text, "reverse").escapes == encoded.escapes
    assert "__SCM_CASE_x_" in encoded.text
    assert encoded.text != raw


def test_escape_discriminator_is_impossible_in_legacy_case_or_dictionary_grammar():
    escaped = quote_prefix(LITERAL_PREFIX)
    assert escaped == "__SCM_CASE_x_5f5f53434d5f4c49545f__"
    assert escaped.isidentifier()
    with pytest.raises(ValueError, match="reserved"):
        Dictionary({"term": escaped.lower()})
    with pytest.raises(ValueError, match="case marker"):
        CipherEngine(MAPPING, marker_version=1).decode(escaped)
    assert CipherEngine(MAPPING).decode(escaped) == LITERAL_PREFIX


def test_raw_quoting_helper_requires_no_mapping_and_preserves_new_neutral_words():
    text = "assemble __SCM_LIT_assemble__\n__SCM_CASE_missing"
    quoted = quote_prefixes(text)
    assert CipherEngine(MAPPING).decode(quoted) == text.replace("assemble ", "fuck ", 1)
    assert quoted.startswith("assemble ")
    assert unquote_prefix(quote_prefix("__ScM_Lit_")) == "__ScM_Lit_"
    with pytest.raises(ValueError, match="exact reserved"):
        quote_prefix("arbitrary hidden data")


@pytest.mark.parametrize(
    "bad",
    [
        ESCAPE_PREFIX + "00" * 10 + "__",
        ESCAPE_PREFIX + "ff" * 10 + "__",
        ESCAPE_PREFIX + "6162636465666768696a" + "__",
        quote_prefix(CASE_PREFIX).lower(),
        quote_prefix(CASE_PREFIX).replace("5f", "5F"),
        quote_prefix(CASE_PREFIX)[:-1],
        quote_prefix(CASE_PREFIX).replace("x_", "X_"),
        ESCAPE_PREFIX + "f" * 49 + "__",
        ESCAPE_PREFIX + "00" + LITERAL_PREFIX.encode().hex() + "__",
    ],
)
def test_invalid_escape_never_becomes_original_data_on_return(bad):
    with pytest.raises(ValueError):
        CipherEngine(MAPPING).decode(bad)
    report = inspect_return(bad.encode(), MAPPING)
    assert report["status"] == "refused" and not report["decodable"]
    assert "invalid_marker" in report["counts"]


def test_valid_escape_does_not_hide_following_invalid_marker():
    text = quote_prefix(CASE_PREFIX) + "\n__SCM_LIT_broken__"
    with pytest.raises(ValueError, match="Damaged literal"):
        CipherEngine(MAPPING).decode(text)
    result = inspect_return(text.encode(), MAPPING)
    finding = next(
        item for item in result["findings"] if item["code"] == "invalid_marker"
    )
    assert finding["location"]["line"] == 2


def test_nested_quoting_restores_one_layer_per_explicit_decode():
    text = "__SCM_CASE_x_bad__ alpha"
    cipher = CipherEngine(MAPPING)
    stages = [text]
    for _ in range(12):
        stages.append(cipher.encode(stages[-1]))
    for before, after in pairwise(stages):
        assert cipher.decode(after) == before


def test_random_adjacency_case_and_identifier_boundaries_preserve_both_protocols():
    randomizer = random.Random(23901)
    fragments = [
        LITERAL_PREFIX,
        CASE_PREFIX,
        "_",
        "__",
        "x_",
        "alpha",
        "fUcK",
        "beta",
        " ",
        "\r\n",
        "\t",
        "'",
        '"',
        "__SCM_CASE_x_",
        "β",
        "\u0301",
    ]
    cipher = CipherEngine(MAPPING)
    for _ in range(400):
        source = "".join(randomizer.choices(fragments, k=20))
        assert cipher.decode(cipher.encode(source)) == source


def test_all_bundled_vocabulary_without_raw_markers_keeps_legacy_encoded_bytes():
    mapping = Dictionary.load()
    text = "\r\n".join([*mapping, *mapping.values(), "fUcK", "SHIT"])
    legacy = CipherEngine(mapping, marker_version=1)
    current = CipherEngine(mapping)
    assert current.encode(text) == legacy.encode(text)
    assert (
        current.decode(legacy.encode(text))
        == legacy.decode(legacy.encode(text))
        == text
    )


@pytest.mark.parametrize("version", [0, 3, True, "2", None])
def test_unknown_marker_versions_are_refused(version):
    with pytest.raises(ValueError):
        CipherEngine(MAPPING, marker_version=version)


def test_expansion_limits_refuse_before_publication_and_scan_counts_prefixes(tmp_path):
    with pytest.raises(ValueError, match="limit"):
        quote_prefixes(LITERAL_PREFIX, limit=len(LITERAL_PREFIX))
    with pytest.raises(ValueError, match="limit"):
        CipherEngine(MAPPING, max_output_chars=20).encode(CASE_PREFIX)
    (tmp_path / "source.txt").write_bytes(LITERAL_PREFIX.encode() * 50)
    with pytest.raises(ValueError, match="limit"):
        service.prepare(
            Workspace(tmp_path, max_bytes=550), "source.txt", dictionary=MAPPING
        )
    assert not (tmp_path / "source_safe.txt").exists()
    scanned = CipherEngine(MAPPING).scan("alpha " + RAW)
    assert scanned["matched_terms"] == 1 and scanned["escaped_prefixes"] == 4
    assert scanned["matches"][0]["start"] == 0


@pytest.mark.parametrize(
    "rules,source",
    [
        (FormatRules(), "\ufeff" + RAW + "\r\nalpha"),
        (
            FormatRules("markdown"),
            "Original __SCM_LIT_alpha__ and alpha.\n\n`__SCM_CASE_invalid__`\n",
        ),
        (
            FormatRules("json", ("/example",)),
            json.dumps({"example": RAW + " alpha", "opaque": RAW}),
        ),
    ],
)
def test_v3_ledgers_bind_format_marker_version_strict_restore_and_edited_validation(
    tmp_path, rules, source
):
    workspace = Workspace(tmp_path)
    original = source.encode()
    (tmp_path / "source.txt").write_bytes(original)
    prepared = service.prepare(
        workspace, "source.txt", dictionary=MAPPING, format_rules=rules
    )
    prepared.apply()
    record = json.loads((tmp_path / ".source_safe.txt.scm_ledger.json").read_bytes())
    assert record["schema_version"] == 3 and record["marker_version"] == 2
    assert record["document"]["rules"] == rules.document()
    assert (
        validate_return(workspace, "source_safe.txt", "source.txt")["status"]
        == "accepted"
    )
    plan = service.prepare(workspace, "source_safe.txt", mode="reverse", strict=True)
    assert plan.result == original
    plan.apply()
    returned = tmp_path / "source_safe.txt"
    returned.write_bytes(returned.read_bytes().replace(b"beta", b"beta added", 1))
    # Prefix-quoted examples are data even when an edited document is restored.
    edited = prepare_restore(
        workspace,
        "source_safe.txt",
        "source.txt",
        output="edited.txt",
        terms="translate",
    )
    edited.apply(edited.review_sha256)
    assert LITERAL_PREFIX.encode() in (tmp_path / "edited.txt").read_bytes()


@pytest.mark.parametrize(
    "rules", [FormatRules(), FormatRules("json", ("/prompt",)), FormatRules("markdown")]
)
def test_historical_ledgers_keep_legacy_grammar_even_for_newly_authored_returns(
    tmp_path, rules
):
    workspace = Workspace(tmp_path)
    text = '{"prompt":"alpha beta"}' if rules.format == "json" else "alpha beta\n"
    (tmp_path / "source.txt").write_text(text, encoding="utf-8", newline="")
    converted = DocumentCodec(MAPPING, rules, marker_version=1).transform(text)
    raw = converted.text.encode()
    (tmp_path / "returned.txt").write_bytes(raw)
    ledger = service._ledger(
        text.encode(),
        raw,
        MAPPING,
        rules=rules,
        context=converted.context,
        marker_version=1,
    )
    (tmp_path / ".returned.txt.scm_ledger.json").write_bytes(ledger)
    assert (
        service.prepare(workspace, "returned.txt", mode="reverse", strict=True).result
        == text.encode()
    )
    report = validate_return(workspace, "returned.txt", "source.txt")
    assert report["status"] == "accepted" and report["marker_version"] == 1
    changed = raw.replace(b"beta", quote_prefix(LITERAL_PREFIX).encode(), 1)
    (tmp_path / "returned.txt").write_bytes(changed)
    with pytest.raises(ValueError, match="case marker"):
        service.prepare(workspace, "returned.txt", mode="reverse")
    assert (
        validate_return(workspace, "returned.txt", "source.txt")["status"] == "refused"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "version_missing",
        "version_bool",
        "version_old",
        "version_unknown",
        "downgrade",
        "context_missing",
    ],
)
def test_invalid_or_downgraded_saved_protocol_is_refused(tmp_path, mutation):
    workspace = Workspace(tmp_path)
    (tmp_path / "source.txt").write_bytes(RAW.encode())
    service.prepare(workspace, "source.txt", dictionary=MAPPING).apply()
    path = tmp_path / ".source_safe.txt.scm_ledger.json"
    record = json.loads(path.read_bytes())
    if mutation == "version_missing":
        record.pop("marker_version")
    elif mutation == "context_missing":
        record.pop("document")
    elif mutation == "downgrade":
        record["schema_version"] = 2
    else:
        record["marker_version"] = {
            "version_bool": True,
            "version_old": 1,
            "version_unknown": 9,
        }[mutation]
    path.write_bytes(json_bytes(record))
    with pytest.raises(ValueError):
        service.prepare(workspace, "source_safe.txt", mode="reverse")


def test_missing_metadata_recovery_reproduces_escape_context_and_refuses_changed_pair(
    tmp_path,
):
    workspace = Workspace(tmp_path)
    raw = (RAW + "\r\nalpha").encode()
    (tmp_path / "original.txt").write_bytes(raw)
    (tmp_path / "returned.txt").write_bytes(
        CipherEngine(MAPPING).encode(raw.decode()).encode()
    )
    service.recover(workspace, "returned.txt", "original.txt", MAPPING, apply=True)
    assert service.verify(workspace, "returned.txt")["original_sha256"] == sha256(raw)
    plan = service.prepare(workspace, "returned.txt", mode="reverse")
    ledger = tmp_path / ".returned.txt.scm_ledger.json"
    ledger.write_bytes(ledger.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed"):
        plan.apply()
    assert not plan.output.exists()


def test_agent_read_modify_create_and_operator_mcp_use_complete_escaping_loop(tmp_path):
    workspace = Workspace(tmp_path)
    raw = b'PREFIX = "__SCM_LIT_"\n\ndef fuck(value):\n    return PREFIX + value + "__"\n\nassert fuck("item") == PREFIX + "item__"\n'
    (tmp_path / "source.py").write_bytes(raw)
    io = AgentFiles(workspace, MAPPING)
    read = io.read("source.py")
    assert read["marker_version"] == 2 and "def assemble" in read["content"]
    write = io.write(
        "source.py",
        read["content"].replace('"item"', '"value"').replace('"item__"', '"value__"'),
    )
    assert write["receipt_saved"] and write["marker_version"] == 2
    subprocess.run(
        [sys.executable, str(tmp_path / "source.py")], check=True, timeout=10
    )
    snippet = quote_prefixes(
        'from source import assemble\nassert assemble("more") == "__SCM_LIT_more__"\n'
    )
    io.write("generated.py", snippet)
    subprocess.run(
        [sys.executable, str(tmp_path / "generated.py")], check=True, timeout=10
    )
    backup = tmp_path / write["history"] / "original.bin"
    assert backup.read_bytes() == raw

    async def exercise():
        async with Client(create_agent(tmp_path, MAPPING)) as client:
            assert {tool.name for tool in await client.list_tools()} == {
                "read_safe_file",
                "write_safe_file",
            }
            response = await client.call_tool(
                "read_safe_file", {"file_path": "source.py"}
            )
            assert response.data["marker_version"] == 2
            result = await client.call_tool(
                "write_safe_file",
                {"file_path": "source.py", "content": response.data["content"]},
            )
            assert result.data["output_saved"]
        async with Client(create_server(tmp_path, MAPPING)) as client:
            preview = await client.call_tool(
                "preview_file", {"source": "source.py", "output": "neutral.py"}
            )
            saved = await client.call_tool(
                "apply_preview", {"plan_id": preview.data["plan_id"]}
            )
            assert saved.data["marker_version"] == 2
            checked = await client.call_tool(
                "validate_returned_file",
                {"source": "neutral.py", "original": "source.py"},
            )
            assert checked.data["status"] == "accepted"

    asyncio.run(exercise())


def setup_view(tmp_path, legacy=False):
    workspace = Workspace(tmp_path)
    (tmp_path / "src").mkdir()
    text = (
        "def fuck(value):\n    return value + 1\n"
        if legacy
        else 'PREFIX = "__SCM_LIT_"\n\ndef fuck(value):\n    return PREFIX + value + "__"\n'
    )
    (tmp_path / "src/core.py").write_text(text, encoding="utf-8", newline="")
    policy = {
        "schema_version": 1,
        "files": {"src/core.py": {"policy": "translate"}},
        "new_files": {"src": "translate"},
    }
    (tmp_path / "policy.json").write_bytes(json_bytes(policy))
    exported = export_view(workspace, "policy.json", "view", MAPPING)
    view_id = exported["view_id"]
    if legacy:
        directory, manifest, _, _, _ = load_view(workspace, view_id)
        original_contract = contract(view_id, MAPPING, policy, marker_version=1)
        manifest["schema_version"] = 1
        manifest.pop("marker_version")
        manifest["contract_sha256"] = put_blob(workspace, directory, original_contract)
        (directory / "manifest.json").write_bytes(json_bytes(manifest))
        (tmp_path / "view" / CONTRACT).write_bytes(original_contract)
    return workspace, view_id, tmp_path / "view"


@pytest.mark.parametrize("recover", [False, True])
def test_scoped_new_marker_source_returns_execute_and_partial_imports_recover(
    tmp_path, monkeypatch, recover
):
    workspace, view_id, view = setup_view(tmp_path)
    assert load_view(workspace, view_id)[1]["marker_version"] == 2
    (view / "src/core.py").write_bytes(
        (view / "src/core.py").read_bytes() + b"# extended parser\n"
    )
    (view / "src/check.py").write_text(
        quote_prefixes(
            'from core import assemble\nassert assemble("node") == "__SCM_LIT_node__"\n'
        ),
        encoding="utf-8",
        newline="",
    )
    subprocess.run([sys.executable, str(view / "src/check.py")], check=True, timeout=10)
    reviewed = view_import.review_import(workspace, view_id)
    assert reviewed["ok"] and all(
        item["marker_version"] == 2 for item in reviewed["return_validation"]["files"]
    )
    if recover:

        def interrupt(path, data, **kwargs):
            if path == tmp_path / "src/core.py":
                raise OSError("retained escape import")
            return atomic_write(path, data, **kwargs)

        with monkeypatch.context() as selected:
            selected.setattr(view_import, "atomic_write", interrupt)
            with pytest.raises(service.OperationError):
                view_import.apply_import(workspace, view_id, reviewed["review_sha256"])
    view_import.apply_import(
        workspace, view_id, reviewed["review_sha256"], recover=recover
    )
    assert view_import.view_status(workspace, view_id)["status"] == "complete"
    subprocess.run(
        [sys.executable, str(tmp_path / "src/check.py")], check=True, timeout=10
    )


def test_legacy_view_baselines_keep_old_contract_and_reject_new_marker_injection(
    tmp_path,
):
    workspace, view_id, view = setup_view(tmp_path, legacy=True)
    report = view_import.review_import(workspace, view_id)
    assert (
        report["ok"] and report["return_validation"]["files"][0]["marker_version"] == 1
    )
    original_contract = (view / CONTRACT).read_bytes()
    (view / "src/check.py").write_text(
        quote_prefixes('VALUE = "__SCM_LIT_"'), encoding="utf-8"
    )
    report = view_import.review_import(workspace, view_id)
    assert not report["ok"] and report["review_sha256"] is None
    assert (view / CONTRACT).read_bytes() == original_contract


@pytest.mark.parametrize(
    "field,value",
    [
        ("marker_version", 1),
        ("marker_version", True),
        ("marker_version", 7),
        ("schema_version", 1),
    ],
)
def test_tampered_view_protocol_is_rejected_before_any_source_write(
    tmp_path, field, value
):
    workspace, view_id, _ = setup_view(tmp_path)
    directory, manifest, _, _, _ = load_view(workspace, view_id)
    original = (tmp_path / "src/core.py").read_bytes()
    manifest[field] = value
    (directory / "manifest.json").write_bytes(json_bytes(manifest))
    with pytest.raises(ValueError):
        view_import.review_import(workspace, view_id)
    assert (tmp_path / "src/core.py").read_bytes() == original


@pytest.mark.parametrize(
    "name",
    ["engine.py", "dictionary.py", "markers.py", "restoration.py", "agent_files.py"],
)
def test_actual_cipher_source_with_literal_protocol_grammar_encodes_and_compiles(name):
    raw = Path(engine.__file__).with_name(name).read_bytes()
    converted = CipherEngine(Dictionary.load()).encode(raw.decode())
    compile(converted, "neutral-engine.py", "exec")
    assert CipherEngine(Dictionary.load()).decode(converted).encode() == raw
