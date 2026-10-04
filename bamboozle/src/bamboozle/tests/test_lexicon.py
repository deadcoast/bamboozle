"""Operator authoring is exercised through compilation and real returned programs."""

import asyncio
import copy
import json
import subprocess
import sys
from importlib.resources import files

import pytest
from fastmcp import Client

from bamboozle.agent_files import AgentFiles
from bamboozle.cipher import CipherEngine
from bamboozle.cli import main
from bamboozle.dictionary import Dictionary
from bamboozle.formats import FormatRules
from bamboozle.lexicon import (
    analyze,
    check_lexicon,
    export_lexicon,
    prepare_compile,
    prepare_edit,
    query_lexicon,
    verify_compilation,
)
from bamboozle.mcp import create_server
from bamboozle.project_views import export_view
from bamboozle.scm_mcp import create_server as agent_server
from bamboozle.service import OperationError, prepare
from bamboozle.utils import json_bytes, parse_json
from bamboozle.workspace import Workspace


def document():
    return {
        "schema_version": 1,
        "mapping_version": "engineering-2",
        "concepts": [
            {
                "id": "verb",
                "kind": "verb",
                "note": "Distinct grammatical forms",
                "forms": [
                    {"bam": "fuck", "boozle": "pulse", "label": "base"},
                    {"bam": "fucked", "boozle": "pulsed", "label": "past"},
                    {"bam": "fucking", "boozle": "pulsing", "label": "progressive"},
                    {"bam": "f*ck", "boozle": "masked_pulse", "label": "masked"},
                ],
            },
            {
                "id": "compound",
                "forms": [{"bam": "clusterfuck", "boozle": "cascade_failure"}],
            },
            {
                "id": "phrase",
                "forms": [{"bam": "son of a bitch", "boozle": "composite_fault"}],
            },
        ],
    }


def setup(tmp_path, doc=None):
    (tmp_path / "source.lexicon.json").write_bytes(
        json_bytes(document() if doc is None else doc)
    )
    return Workspace(tmp_path)


def test_explicit_forms_phrases_literals_mixed_case_and_identifier_boundaries(tmp_path):
    workspace = setup(tmp_path)
    plan = prepare_compile(workspace, "source.lexicon.json", "runtime.dictionary.json")
    assert plan.report()["change_counts"] == {"added": 6, "changed": 0, "removed": 0}
    plan.apply(plan.review_sha256)
    engine = CipherEngine(Dictionary.load(tmp_path / "runtime.dictionary.json"))
    text = "Fuck fucked fucking f*ck son of a bitch clusterfuck; pulse; FuCk; prefuck fuck_count"
    encoded = engine.encode(text)
    assert encoded.startswith(
        "Pulse pulsed pulsing masked_pulse composite_fault cascade_failure;"
    )
    assert "__SCM_LIT_pulse__" in encoded and "__SCM_CASE_" in encoded
    assert "prefuck fuck_count" in encoded
    assert engine.decode(encoded) == text
    assert verify_compilation(workspace, "runtime.dictionary.json")[
        "current_inputs_unchanged"
    ]


@pytest.mark.parametrize("profile,count", [("default", 415), ("profanity", 310)])
def test_export_compiles_entire_shipped_profile_preserving_originals(
    tmp_path, profile, count
):
    workspace = Workspace(tmp_path)
    mapping = Dictionary.load(profile=profile)
    mapping.save(tmp_path / "base.json")
    report = export_lexicon(workspace, mapping, "shipped.lexicon.json", "custom-1")
    assert report["forms"] == count
    plan = prepare_compile(
        workspace, "shipped.lexicon.json", "shipped.dictionary.json", base="base.json"
    )
    assert plan.report()["change_count"] == 0
    plan.apply(plan.review_sha256)
    compiled = Dictionary.load(tmp_path / "shipped.dictionary.json")
    assert dict(compiled) == dict(mapping)
    if profile == "default":
        original = Dictionary.from_document(
            parse_json(files("bamboozle").joinpath("cipher_dict.json").read_bytes())
        )
        assert len(original) == 110
        assert all(compiled[source] == target for source, target in original.items())
    with pytest.raises(FileExistsError):
        export_lexicon(workspace, mapping, "shipped.lexicon.json", "custom-2")


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda d: d.update(schema_version=True), "schema"),
        (lambda d: d.update(extra=True), "schema"),
        (lambda d: d.update(mapping_version=""), "version"),
        (lambda d: d.update(concepts={}), "limit"),
        (lambda d: d["concepts"][0].update(id="bad/id"), "id"),
        (lambda d: d["concepts"][1].update(id="verb"), "duplicate_id"),
        (lambda d: d["concepts"][0].update(forms=[]), "schema"),
        (lambda d: d["concepts"][0].update(kind=12), "schema"),
        (lambda d: d["concepts"][0].update(note="x" * 2049), "schema"),
        (lambda d: d["concepts"][0]["forms"][0].update(label=None), "schema"),
        (
            lambda d: d["concepts"][0]["forms"][0].update(boozle="fucked"),
            "vocabulary_overlap",
        ),
        (
            lambda d: d["concepts"][0]["forms"][0].update(boozle="pulsed"),
            "duplicate_boozle",
        ),
        (lambda d: d["concepts"][0]["forms"][0].update(bam="fucked"), "duplicate_bam"),
        (
            lambda d: d["concepts"][0]["forms"][0].update(label="past"),
            "duplicate_label",
        ),
        (lambda d: d["concepts"][0]["forms"][0].update(unknown=True), "schema"),
    ],
)
def test_schema_diagnostics_are_located_and_never_publish(tmp_path, change, code):
    doc = document()
    change(doc)
    workspace = setup(tmp_path, doc)
    checked = check_lexicon(workspace, "source.lexicon.json")
    assert checked["ok"] is False
    assert code in {item["code"] for item in checked["diagnostics"]}
    assert all("path" in item and "message" in item for item in checked["diagnostics"])
    plan = prepare_compile(workspace, "source.lexicon.json", "invalid.dictionary.json")
    with pytest.raises(ValueError):
        plan.apply(plan.review_sha256)
    assert not (tmp_path / "invalid.dictionary.json").exists()


@pytest.mark.parametrize(
    "term",
    [
        "",
        " fuck",
        "fuck ",
        "FuCk",
        "fuck\n",
        "a\x00b",
        "__scm_lit_pulse__",
        "__scm_case_a_x__",
        "x" * 513,
        12,
        "\ud800",
    ],
)
def test_dictionary_and_lexicon_share_term_admission(term):
    doc = document()
    doc["concepts"][0]["forms"][0]["bam"] = term
    assert analyze(doc).diagnostics[0]["code"] == "term"
    with pytest.raises(ValueError):
        Dictionary({term: "pulse"})


def test_collision_locations_diagnostic_limits_and_total_form_bound():
    doc = document()
    doc["concepts"] = [
        {"id": f"entry{i}", "forms": [{"bam": "source", "boozle": "target"}]}
        for i in range(120)
    ]
    result = analyze(doc).report()
    assert result["error_count"] == 238
    assert len(result["diagnostics"]) == 100
    assert not result["diagnostics_complete"]
    assert result["diagnostics"][0]["related_path"] == "/concepts/0/forms/0/bam"
    doc["concepts"][0]["forms"] *= 10001
    assert analyze(doc).diagnostics[0]["code"] == "limit"


def test_unicode_folded_aliases_cannot_collapse_reverse_mapping():
    doc = document()
    doc["concepts"][0]["forms"] = [
        {"bam": "straße", "boozle": "route"},
        {"bam": "strasse", "boozle": "track"},
    ]
    assert "duplicate_bam" in {item["code"] for item in analyze(doc).diagnostics}


@pytest.mark.parametrize("bom_inputs", [("source",), ("base",), ("source", "base")])
def test_provenance_preserves_and_reparses_utf8_bom_inputs(tmp_path, bom_inputs):
    workspace = setup(tmp_path)
    Dictionary({"fuck": "pulse"}, "bom-baseline").save(tmp_path / "base.json")
    paths = {"source": tmp_path / "source.lexicon.json", "base": tmp_path / "base.json"}
    for name in bom_inputs:
        # Windows-created authoring files can carry a BOM and noncanonical layout.
        paths[name].write_bytes(
            b"\xef\xbb\xbf" + paths[name].read_bytes().replace(b"\n", b"\r\n")
        )
    original = {name: path.read_bytes() for name, path in paths.items()}
    compiled = prepare_compile(
        workspace, "source.lexicon.json", "bom.dictionary.json", base="base.json"
    )
    compiled.apply(compiled.review_sha256)
    assert verify_compilation(workspace, "bom.dictionary.json")[
        "current_inputs_unchanged"
    ]
    record = parse_json(compiled.metadata_path.read_bytes())
    for name, path in paths.items():
        assert record["snapshots"][path.name].encode("utf-8") == original[name]


def test_query_both_vocabularies_pagination_and_stale_pin(tmp_path):
    workspace = setup(tmp_path)
    first = query_lexicon(
        workspace, "source.lexicon.json", query="PULSE", side="boozle", limit=1
    )
    assert first["total"] == 3 and len(first["entries"]) == 1
    second = query_lexicon(
        workspace,
        "source.lexicon.json",
        query="PULSE",
        side="boozle",
        offset=first["next_offset"],
        limit=1,
        expected_sha256=first["lexicon_sha256"],
    )
    assert second["entries"] != first["entries"]
    assert (
        query_lexicon(workspace, "source.lexicon.json", query="f*ck", side="bam")[
            "total"
        ]
        == 1
    )
    (tmp_path / "source.lexicon.json").write_bytes(json_bytes(document()) + b" ")
    with pytest.raises(ValueError, match="changed"):
        query_lexicon(
            workspace, "source.lexicon.json", expected_sha256=first["lexicon_sha256"]
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"offset": True},
        {"side": "reverse"},
        {"query": "a" * 513},
    ],
)
def test_query_bounds(tmp_path, arguments):
    with pytest.raises(ValueError):
        query_lexicon(setup(tmp_path), "source.lexicon.json", **arguments)


def test_exact_diff_pages_digest_and_base_staleness(tmp_path):
    workspace = setup(tmp_path)
    base = Dictionary({"fuck": "impulse", "obsolete": "old_signal"}, "engineering-1")
    base.save(tmp_path / "base.json")
    first = prepare_compile(
        workspace,
        "source.lexicon.json",
        "v2.dictionary.json",
        base="base.json",
        limit=2,
    )
    all_changes = list(first.report()["changes"])
    page = first.report()["next_offset"]
    while page is not None:
        next_page = prepare_compile(
            workspace,
            "source.lexicon.json",
            "v2.dictionary.json",
            base="base.json",
            offset=page,
            limit=2,
        )
        assert next_page.review_sha256 == first.review_sha256
        all_changes.extend(next_page.report()["changes"])
        page = next_page.report()["next_offset"]
    assert len(all_changes) == 7
    assert first.report()["change_counts"] == {"added": 5, "changed": 1, "removed": 1}
    assert {
        "source": "fuck",
        "before": "impulse",
        "after": "pulse",
        "change": "changed",
    } in all_changes
    assert {
        "source": "obsolete",
        "before": "old_signal",
        "after": None,
        "change": "removed",
    } in all_changes
    (tmp_path / "base.json").write_bytes(json_bytes(base.document()) + b" ")
    with pytest.raises(ValueError, match="changed"):
        first.apply(first.review_sha256)
    assert not (tmp_path / "v2.dictionary.json").exists()


@pytest.mark.parametrize(
    "failure", ["source", "digest", "destination", "receipt", "version", "output_input"]
)
def test_review_refuses_stale_inputs_replay_wrong_digest_and_clobber(tmp_path, failure):
    workspace = setup(tmp_path)
    plan = prepare_compile(workspace, "source.lexicon.json", "v2.dictionary.json")
    expected = plan.review_sha256
    if failure == "source":
        (tmp_path / "source.lexicon.json").write_bytes(json_bytes(document()) + b" ")
    elif failure == "digest":
        expected = "0" * 64
    elif failure == "destination":
        plan.output.write_text("retain me")
    elif failure == "receipt":
        plan.metadata_path.write_text("retain me")
    elif failure == "version":
        mapping = analyze(document()).dictionary
        assert mapping is not None
        mapping.save(tmp_path / "base.json")
        with pytest.raises(ValueError, match="new mapping_version"):
            prepare_compile(
                workspace, "source.lexicon.json", "v2.dictionary.json", base="base.json"
            )
        return
    else:
        Dictionary({"x": "y"}).save(tmp_path / "same.dictionary.json")
        with pytest.raises(ValueError, match="separate"):
            prepare_compile(
                workspace,
                "source.lexicon.json",
                "same.dictionary.json",
                base="same.dictionary.json",
            )
        return
    with pytest.raises((ValueError, FileExistsError)):
        plan.apply(expected)


def test_interrupted_publication_retains_dictionary_and_explicitly_recovers(
    tmp_path, monkeypatch
):
    import bamboozle.restoration as publishing

    workspace = setup(tmp_path)
    plan = prepare_compile(workspace, "source.lexicon.json", "v2.dictionary.json")
    real_write = publishing.atomic_write

    def fail_receipt(path, data, **kwargs):
        if path == plan.metadata_path:
            raise OSError("injected receipt failure")
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(publishing, "atomic_write", fail_receipt)
    with pytest.raises(OperationError) as failure:
        plan.apply(plan.review_sha256)
    assert (
        failure.value.report["output_saved"]
        and not failure.value.report["metadata_saved"]
    )
    assert plan.output.read_bytes() == plan.result
    monkeypatch.setattr(publishing, "atomic_write", real_write)
    resumed = prepare_compile(
        workspace, "source.lexicon.json", "v2.dictionary.json", recover_metadata=True
    )
    assert resumed.review_sha256 == plan.review_sha256
    assert (
        resumed.apply(plan.review_sha256, recover_metadata=True)["status"]
        == "metadata_recovered"
    )
    with pytest.raises(FileExistsError):
        plan.apply(plan.review_sha256)


def test_provenance_reproduces_snapshots_and_separates_later_input_drift(tmp_path):
    workspace = setup(tmp_path)
    plan = prepare_compile(workspace, "source.lexicon.json", "v2.dictionary.json")
    plan.apply(plan.review_sha256)
    (tmp_path / "source.lexicon.json").write_text("changed authoring file")
    verified = verify_compilation(workspace, "v2.dictionary.json")
    assert verified["ok"] and not verified["current_inputs_unchanged"]
    record = parse_json(plan.metadata_path.read_bytes())
    record["inputs"]["source.lexicon.json"] = "0" * 64
    plan.metadata_path.write_bytes(json_bytes(record))
    with pytest.raises(ValueError, match="snapshot hash"):
        verify_compilation(workspace, "v2.dictionary.json")


@pytest.mark.parametrize(
    "mutation", ["dictionary", "lexicon", "changes", "snapshot", "version"]
)
def test_modified_artifact_and_receipt_are_not_verified(tmp_path, mutation):
    workspace = setup(tmp_path)
    plan = prepare_compile(workspace, "source.lexicon.json", "v2.dictionary.json")
    plan.apply(plan.review_sha256)
    record = parse_json(plan.metadata_path.read_bytes())
    if mutation == "dictionary":
        assert plan.result is not None
        plan.output.write_bytes(plan.result + b" ")
    elif mutation == "lexicon":
        record["lexicon"]["concepts"][0]["forms"][0]["boozle"] = "different"
    elif mutation == "changes":
        record["changes"] = []
    elif mutation == "snapshot":
        record["snapshots"]["source.lexicon.json"] = "{}"
    else:
        record["schema_version"] = True
    plan.metadata_path.write_bytes(json_bytes(record))
    with pytest.raises(ValueError):
        verify_compilation(workspace, "v2.dictionary.json")


def patch_document():
    return {
        "schema_version": 1,
        "mapping_version": "engineering-3",
        "upsert": [
            {
                "id": "verb",
                "kind": "verb",
                "forms": [
                    {"bam": "fuck", "boozle": "impulse"},
                    {"bam": "fucked", "boozle": "impulsed"},
                ],
            }
        ],
        "remove": ["phrase"],
    }


def test_reviewed_authoring_upsert_remove_and_patch_staleness(tmp_path):
    workspace = setup(tmp_path)
    original = (tmp_path / "source.lexicon.json").read_bytes()
    patch = tmp_path / "changes.lexicon-patch.json"
    patch.write_bytes(json_bytes(patch_document()))
    plan = prepare_edit(
        workspace, "source.lexicon.json", patch.name, "edited.lexicon.json"
    )
    assert plan.report()["diff_complete"] and "impulse" in plan.report()["diff"]
    assert not plan.output.exists()
    patch.write_bytes(patch.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed"):
        plan.apply(plan.review_sha256)
    plan = prepare_edit(
        workspace, "source.lexicon.json", patch.name, "edited.lexicon.json"
    )
    plan.apply(plan.review_sha256)
    compiled = prepare_compile(
        workspace, "edited.lexicon.json", "edited.dictionary.json"
    )
    compiled.apply(compiled.review_sha256)
    mapping = Dictionary.load(compiled.output)
    assert mapping.version == "engineering-3" and mapping["fuck"] == "impulse"
    assert "son of a bitch" not in mapping and "fucking" not in mapping
    assert (tmp_path / "source.lexicon.json").read_bytes() == original


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(remove=["absent"]),
        lambda p: p.update(remove=["phrase", "phrase"]),
        lambda p: p.update(remove=["verb"]),
        lambda p: p["upsert"].append(copy.deepcopy(p["upsert"][0])),
        lambda p: p.update(schema_version=True),
    ],
)
def test_invalid_patch_never_writes(tmp_path, mutate):
    workspace = setup(tmp_path)
    patch = patch_document()
    mutate(patch)
    (tmp_path / "changes.lexicon-patch.json").write_bytes(json_bytes(patch))
    with pytest.raises(ValueError):
        prepare_edit(
            workspace,
            "source.lexicon.json",
            "changes.lexicon-patch.json",
            "edited.lexicon.json",
        )
    assert not (tmp_path / "edited.lexicon.json").exists()


@pytest.mark.parametrize(
    "name",
    [
        "source.lexicon.json",
        "changes.lexicon-patch.json",
        "runtime.dictionary.json",
        ".runtime.dictionary.json.lexicon-receipt.json",
    ],
)
def test_authoring_and_compiler_artifacts_stay_outside_agent_tools_and_views(
    tmp_path, name
):
    (tmp_path / name).write_text("source secret")
    files_api = AgentFiles(Workspace(tmp_path), Dictionary({"secret": "neutral"}))
    with pytest.raises(ValueError, match="Private"):
        files_api.read(name)
    with pytest.raises(ValueError, match="Private"):
        files_api.write(name, "replacement")
    (tmp_path / "policy.json").write_bytes(
        json_bytes(
            {"schema_version": 1, "files": {name: {"policy": "copy"}}, "new_files": {}}
        )
    )
    with pytest.raises(ValueError, match="Private"):
        export_view(Workspace(tmp_path), "policy.json", "agent", files_api.dictionary)


def test_new_dictionary_does_not_reinterpret_saved_document_ledger(tmp_path):
    workspace = setup(tmp_path)
    old = Dictionary({"fuck": "pulse"}, "old")
    raw = b'{ "prompt" : "f\\u0075ck", "id": "fuck" }\r\n'
    (tmp_path / "settings.json").write_bytes(raw)
    prepare(
        workspace,
        "settings.json",
        output="encoded.json",
        dictionary=old,
        format_rules=FormatRules("json", ("/prompt",)),
    ).apply()
    new_document = document()
    new_document["concepts"][0]["forms"][0]["boozle"] = "impulse"
    (tmp_path / "source.lexicon.json").write_bytes(json_bytes(new_document))
    plan = prepare_compile(workspace, "source.lexicon.json", "new.dictionary.json")
    plan.apply(plan.review_sha256)
    prepare(
        workspace, "encoded.json", output="exact.json", mode="reverse", strict=True
    ).apply()
    assert (tmp_path / "exact.json").read_bytes() == raw


def test_cli_author_edit_compile_encode_generated_program_decode_execute(
    tmp_path, capsys
):
    (tmp_path / "base.json").write_bytes(
        json_bytes(Dictionary({"fuck": "pulse"}, "baseline").document())
    )

    def cli(*args, expected=0):
        assert main(["--root", str(tmp_path), *args]) == expected
        return json.loads(capsys.readouterr().out)

    cli(
        "lexicon-export",
        "--dict",
        "base.json",
        "--mapping-version",
        "code-2",
        "-o",
        "source.lexicon.json",
    )
    found = cli("lexicon-query", "source.lexicon.json", "--query", "pulse")
    identifier = found["entries"][0]["id"]
    (tmp_path / "changes.lexicon-patch.json").write_bytes(
        json_bytes(
            {
                "schema_version": 1,
                "mapping_version": "code-2",
                "remove": [],
                "upsert": [
                    {"id": identifier, "forms": [{"bam": "fuck", "boozle": "impulse"}]},
                    {"id": "state", "forms": [{"bam": "shit", "boozle": "residue"}]},
                ],
            }
        )
    )
    args = (
        "lexicon-edit",
        "source.lexicon.json",
        "--patch",
        "changes.lexicon-patch.json",
        "-o",
        "code.lexicon.json",
    )
    edited = cli(*args)
    cli(*args, "--apply", "--review-sha256", edited["review_sha256"])
    args = (
        "lexicon-compile",
        "code.lexicon.json",
        "--base",
        "base.json",
        "-o",
        "code.dictionary.json",
    )
    reviewed = cli(*args)
    cli(*args, "--apply", "--review-sha256", reviewed["review_sha256"])
    assert cli("lexicon-verify", "code.dictionary.json")["status"] == "verified"
    (tmp_path / "program.py").write_text(
        "def fuck(value):\n    return value + 1\n", encoding="utf-8"
    )
    cli("encode", "program.py", "--dict", "code.dictionary.json", "-o", "neutral.py")
    assert "def impulse" in (tmp_path / "neutral.py").read_text()
    (tmp_path / "returned.py").write_text(
        "def impulse(value):\n    residue = value * 3\n    return residue + 2\n\nassert impulse(4) == 14\nprint('runtime-ok')\n",
        encoding="utf-8",
    )
    cli(
        "decode",
        "returned.py",
        "--ledger",
        ".neutral.py.scm_ledger.json",
        "-o",
        "restored.py",
    )
    restored = (tmp_path / "restored.py").read_text()
    assert "def fuck" in restored and "shit = value * 3" in restored
    result = subprocess.run(
        [sys.executable, "restored.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert result.stdout.strip() == "runtime-ok"


def test_operator_mcp_author_compile_formatted_agent_return_and_boundary(tmp_path):
    workspace = Workspace(tmp_path)
    server = create_server(tmp_path, Dictionary({"fuck": "pulse"}, "original"))

    async def cycle():
        async with Client(server) as client:

            async def call(name, **kwargs):
                result = await client.call_tool(name, kwargs)
                content = result.content[0]
                assert content.type == "text"
                return json.loads(content.text)

            await call(
                "export_dictionary_lexicon",
                output="draft.lexicon.json",
                mapping_version="mcp-2",
            )
            assert (
                await call("check_dictionary_lexicon", source="draft.lexicon.json")
            )["ok"]
            row = (await call("query_dictionary_lexicon", source="draft.lexicon.json"))[
                "entries"
            ][0]
            (tmp_path / "edit.lexicon-patch.json").write_bytes(
                json_bytes(
                    {
                        "schema_version": 1,
                        "mapping_version": "mcp-2",
                        "remove": [],
                        "upsert": [
                            {
                                "id": row["id"],
                                "forms": [{"bam": "fuck", "boozle": "impulse"}],
                            }
                        ],
                    }
                )
            )
            edit = await call(
                "preview_lexicon_edit",
                source="draft.lexicon.json",
                patch="edit.lexicon-patch.json",
                output="mcp.lexicon.json",
            )
            await call("apply_preview", plan_id=edit["plan_id"])
            compiled = await call(
                "preview_lexicon_compilation",
                source="mcp.lexicon.json",
                output="mcp.dictionary.json",
            )
            await call("apply_preview", plan_id=compiled["plan_id"])
            assert (
                await call(
                    "verify_dictionary_compilation", source="mcp.dictionary.json"
                )
            )["ok"]
            (tmp_path / "guide.md").write_text(
                "Fuck **fuck** `fuck`\n", encoding="utf-8"
            )
            encoded = await call(
                "preview_file",
                source="guide.md",
                output="neutral.md",
                dictionary="mcp.dictionary.json",
                document_format="markdown",
            )
            await call("apply_preview", plan_id=encoded["plan_id"])
            assert (
                tmp_path / "neutral.md"
            ).read_text() == "Impulse **impulse** `fuck`\n"
            (tmp_path / "return.md").write_text(
                "Impulse **impulse** `fuck`\nMore impulse.\n", encoding="utf-8"
            )
            decoded = await call(
                "preview_file",
                source="return.md",
                output="restored.md",
                reverse=True,
                ledger=".neutral.md.scm_ledger.json",
            )
            await call("apply_preview", plan_id=decoded["plan_id"])
            assert (
                tmp_path / "restored.md"
            ).read_text() == "Fuck **fuck** `fuck`\nMore fuck.\n"
        async with Client(
            agent_server(
                workspace.root, Dictionary.load(tmp_path / "mcp.dictionary.json")
            )
        ) as client:
            assert {tool.name for tool in await client.list_tools()} == {
                "read_safe_file",
                "write_safe_file",
            }
            rejected = await client.call_tool(
                "read_safe_file",
                {"file_path": "mcp.lexicon.json"},
                raise_on_error=False,
            )
            assert rejected.is_error

    asyncio.run(cycle())
