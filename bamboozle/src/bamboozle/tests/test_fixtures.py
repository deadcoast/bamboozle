"""Generated neutral data is qualified through the complete returned-code workflow."""

import asyncio
import copy
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from bamboozle import project_views, view_import
from bamboozle.agent_files import AgentFiles
from bamboozle.cli import main
from bamboozle.dictionary import Dictionary
from bamboozle.fixtures import (
    GENERATED_FILES,
    TEMPLATE_CASES,
    TEMPLATE_CONTRACT,
    TEMPLATE_SCHEMA,
    FixtureError,
    artifacts,
    format_signature,
    prepare_fixture,
    preview_fixture,
    read_rules,
    validate_fixture,
)
from bamboozle.mcp import create_server
from bamboozle.project_views import blob, export_view, load_view, read_policy
from bamboozle.service import OperationError
from bamboozle.utils import json_bytes, parse_json
from bamboozle.view_import import apply_import, review_import, view_status
from bamboozle.workspace import Workspace

SCENARIO = runpy.run_path(
    str(Path(__file__).resolve().parents[3] / "scripts/fixture_scenario.py")
)
source_document = SCENARIO["source_document"]
rules_document = SCENARIO["rules_document"]


def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/renderer.py").write_text(
        "def fuck(record, values):\n    return record['prompt'].format_map(values)\n",
        encoding="utf-8",
    )
    (tmp_path / "templates.json").write_bytes(
        b"\xef\xbb\xbf" + json_bytes(source_document()).replace(b"\n", b"\r\n")
    )
    (tmp_path / "templates.fixture-rules.json").write_bytes(
        json_bytes(rules_document())
    )
    policy = {
        "schema_version": 3,
        "files": {
            "templates.json": {
                "policy": "fixture",
                "rules": "templates.fixture-rules.json",
            },
            "src/renderer.py": {"policy": "translate"},
        },
        "new_files": {"src": "translate", "tests": "copy"},
    }
    (tmp_path / "policy.json").write_bytes(json_bytes(policy))
    return Workspace(tmp_path)


def export(tmp_path):
    workspace = project(tmp_path)
    result = export_view(
        workspace, "policy.json", "neutral", Dictionary({"fuck": "assemble"})
    )
    return workspace, result, Workspace(tmp_path / "neutral")


def test_generation_preserves_tree_placeholders_references_optional_fields_and_newlines(
    tmp_path,
):
    workspace = project(tmp_path)
    before = workspace.read("templates.json")
    fixture, inputs = prepare_fixture(
        workspace, "templates.json", "templates.fixture-rules.json"
    )
    generated = parse_json(fixture.data)
    original = source_document()
    assert generated["templates"][0]["prompt"].startswith("Operator {actor_a}")
    assert "notes" not in generated["templates"][0]
    for old, new in zip(original["templates"], generated["templates"], strict=True):
        assert format_signature(old.pop("prompt"), "format") == format_signature(
            new.pop("prompt"), "format"
        )
        old.pop("notes", None)
        new.pop("notes", None)
    assert original == generated
    assert workspace.read("templates.json") == before
    assert set(inputs) == {
        "templates.json",
        "templates.fixture-rules.json",
        "src/renderer.py",
    }
    assert len(fixture.cases) == 8 and fixture.cases[0]["expect"] == []
    assert all(
        b"PRIVATE_SOURCE" not in value and b"PRIVATE_NOTE" not in value
        for value in artifacts({"templates.json": fixture}).values()
    )
    assert fixture.contract["consumers"][0]["line"] == 1


def test_preview_is_bounded_readonly_and_repeatable(tmp_path):
    workspace = project(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    first = preview_fixture(workspace, "templates.json", "templates.fixture-rules.json")
    assert first == preview_fixture(
        workspace, "templates.json", "templates.fixture-rules.json"
    )
    assert first["ok"] and first["preview_complete"] and not first["output_saved"]
    assert {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize(
    "mutation,code",
    [
        (
            lambda d: d["templates"][0].update(secret="private new field"),
            "@unclassified",
        ),
        (lambda d: d.update(secret="private root field"), "@unclassified"),
        (lambda d: d["templates"][0].update(variables=["actor_a"]), "variables"),
        (lambda d: d["templates"][0].update(version=True), "version"),
        (lambda d: d["templates"][0].update(duration=-1), "duration"),
        (lambda d: d["templates"][0].update(roles=[]), "roles"),
        (lambda d: d["templates"][0].pop("role_order"), "ordered"),
        (lambda d: d["templates"][0].update(links=["missing"]), "@reference"),
        (lambda d: d["templates"][0].update(id="template_018"), "@id"),
        (lambda d: d["templates"][0].update(prompt=17), "@text"),
        (lambda d: d["templates"][0].update(category="unknown"), "category"),
        (lambda d: d.update(templates=[]), "@records"),
    ],
)
def test_invalid_source_never_exports_anything(tmp_path, mutation, code):
    workspace = project(tmp_path)
    document = source_document()
    mutation(document)
    (tmp_path / "templates.json").write_bytes(json_bytes(document))
    report = preview_fixture(
        workspace, "templates.json", "templates.fixture-rules.json"
    )
    assert not report["ok"] and code in report["codes"]
    with pytest.raises(FixtureError):
        export_view(workspace, "policy.json", "neutral")
    assert not (tmp_path / "neutral").exists() and not (tmp_path / ".local").exists()


@pytest.mark.parametrize(
    "neutral",
    [
        "Operator {actor_a} meets {actor_b!s} for {duration:.1f}.\nDone.",
        "Operator {actor_a} meets {actor_b!s} for {duration:.2f} at {{station}}.\nConfirm {actor_a}.",
        "Operator {actor_a} meets {actor_b} for {duration:.1f} at {{station}}.\nConfirm {actor_a}.",
        "Operator {actor_a} meets {actor_b!s} for {duration:.1f} at {{station}}. Confirm {actor_a}.",
        "Operator {actor_a} meets {actor_b!s} for {duration:.1f} at station.\nConfirm {actor_a}.",
    ],
)
def test_format_multiplicity_specifiers_conversions_escapes_and_lines_are_invariants(
    tmp_path, neutral
):
    workspace = project(tmp_path)
    rules = rules_document()
    rules["text"]["/prompt"]["template"] = neutral
    (tmp_path / "templates.fixture-rules.json").write_bytes(json_bytes(rules))
    report = preview_fixture(
        workspace, "templates.json", "templates.fixture-rules.json"
    )
    assert not report["ok"] and report["codes"] == ["@format"]


@pytest.mark.parametrize(
    "text", ["{}", "{0}", "{actor.name}", "{actor[0]}", "{actor!z}", "{actor", "actor}"]
)
def test_unsupported_or_malformed_placeholder_syntax_fails_explicitly(text):
    with pytest.raises(ValueError):
        format_signature(text, "format")


def test_nested_format_fields_are_counted_and_preserved():
    signature = format_signature("{value:{width}.{precision}f} {value!r}", "format")
    assert {field[0] for field in signature["fields"]} == {
        "value",
        "width",
        "precision",
    }
    assert signature != format_signature("{value:{width}.{precision}f}", "format")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(schema_version=True),
        lambda r: r.update(extra=True),
        lambda r: r.update(records="templates"),
        lambda r: r["text"]["/prompt"].update(optional="yes"),
        lambda r: r["checks"][0].update(kind="eval"),
        lambda r: r["checks"][0].update(minimum=9, maximum=2),
        lambda r: r["checks"][0].update(min_length=2),
        lambda r: r["checks"].append(copy.deepcopy(r["checks"][0])),
        lambda r: r["references"][0].update(many="yes"),
        lambda r: r["consumers"][0].update(symbol="invalid-symbol"),
        lambda r: r["cases"][0].update(expect=["nonexistent_check"]),
        lambda r: r["cases"][0].update(name="baseline"),
        lambda r: r["cases"][0].update(
            set={"/constraints": {}, "/constraints/ordered": True}
        ),
        lambda r: r["cases"][0].update(remove=["/duration"]),
    ],
)
def test_strict_rules_reject_unknown_ambiguous_and_conflicting_configuration(mutate):
    rules = rules_document()
    mutate(rules)
    with pytest.raises((ValueError, TypeError)):
        read_rules(json_bytes(rules))


@pytest.mark.parametrize(
    "mutation,message",
    [
        (
            lambda r: r.update(
                literal_dependencies=[
                    {"field": "/prompt", "reason": "consumer compares literal prose"}
                ]
            ),
            "literal-prose",
        ),
        (lambda r: r["consumers"][0].update(symbol="absent"), "absent"),
        (lambda r: r["consumers"][0].update(fields=["/absent"]), "does not occur"),
        (
            lambda r: r["text"]["/prompt"]["variants"].update(unknown="neutral"),
            "unknown record",
        ),
        (lambda r: r["cases"][1].update(expect=[]), "exactly"),
    ],
)
def test_generation_refuses_unresolved_dependencies_unbound_consumers_and_incorrect_cases(
    tmp_path, mutation, message
):
    workspace = project(tmp_path)
    rules = rules_document()
    mutation(rules)
    (tmp_path / "templates.fixture-rules.json").write_bytes(json_bytes(rules))
    with pytest.raises(ValueError, match=message):
        prepare_fixture(workspace, "templates.json", "templates.fixture-rules.json")


def test_view_contracts_are_neutral_bound_to_encoded_call_paths_and_reproducible(
    tmp_path,
):
    workspace, exported, view = export(tmp_path)
    directory, manifest, _, _, _ = load_view(workspace, exported["view_id"])
    assert set(manifest["generated_sha256"]) == set(GENERATED_FILES)
    assert blob(
        workspace, directory, manifest["files"]["templates.json"]["original_sha256"]
    ) == workspace.read("templates.json")
    assert "`assemble`" in view.read(TEMPLATE_CONTRACT).decode()
    for name in (*GENERATED_FILES, "templates.json"):
        assert b"PRIVATE_SOURCE" not in view.read(
            name
        ) and b"PRIVATE_NOTE" not in view.read(name)
    assert not (view.root / "templates.fixture-rules.json").exists()
    assert validate_fixture(view, "templates.json", TEMPLATE_SCHEMA)["ok"]
    corpus = parse_json(view.read(TEMPLATE_CASES))["fixtures"]["templates.json"]
    for index, case in enumerate(corpus):
        (tmp_path / f"case{index}.json").write_bytes(json_bytes(case["document"]))
        result = validate_fixture(
            workspace,
            f"case{index}.json",
            "neutral/" + TEMPLATE_SCHEMA,
            fixture="templates.json",
        )
        assert result["codes"] == case["expect"]


@pytest.mark.parametrize("name", GENERATED_FILES)
def test_generated_controls_cannot_be_changed_removed_or_imported(tmp_path, name):
    workspace, exported, view = export(tmp_path)
    (view.root / name).write_bytes(b"changed")
    with pytest.raises(ValueError, match="contract"):
        review_import(workspace, exported["view_id"])
    with pytest.raises(ValueError, match="generated"):
        bad = {
            "schema_version": 3,
            "files": {name: {"policy": "copy"}},
            "new_files": {},
        }
        (tmp_path / "bad.json").write_bytes(json_bytes(bad))
        export_view(workspace, "bad.json", "other")


def test_source_rules_are_private_and_saved_rule_changes_do_not_reinterpret_view(
    tmp_path,
):
    workspace, exported, _ = export(tmp_path)
    with pytest.raises(ValueError, match="Private"):
        AgentFiles(workspace, Dictionary({})).read("templates.fixture-rules.json")
    (tmp_path / "templates.fixture-rules.json").write_bytes(b"changed future rules")
    assert (
        load_view(workspace, exported["view_id"])[1]["view_id"] == exported["view_id"]
    )
    # Production data is a dependency of returned code, although never overwritten.
    (tmp_path / "templates.json").write_bytes(workspace.read("templates.json") + b" ")
    assert not review_import(workspace, exported["view_id"])["ok"]


def test_manifest_rejects_changed_generator_snapshot(tmp_path):
    workspace, exported, _ = export(tmp_path)
    directory, manifest, _, _, _ = load_view(workspace, exported["view_id"])
    fingerprint = manifest["files"]["templates.json"]["fixture_rules_sha256"]
    (directory / "blobs" / f"{fingerprint}.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest"):
        load_view(workspace, exported["view_id"])


def test_excluded_or_noncallable_consumer_cannot_authorize_a_contract(tmp_path):
    workspace = project(tmp_path)
    policy = parse_json(workspace.read("policy.json"))
    policy["files"]["src/renderer.py"] = {"policy": "exclude"}
    (tmp_path / "policy.json").write_bytes(json_bytes(policy))
    with pytest.raises(ValueError, match="selected"):
        export_view(workspace, "policy.json", "neutral")
    (tmp_path / "src/renderer.py").write_text("class fuck: pass")
    with pytest.raises(TypeError, match="callable"):
        prepare_fixture(workspace, "templates.json", "templates.fixture-rules.json")


def test_rules_cannot_be_silently_enabled_in_old_policy():
    for version in (1, 2):
        with pytest.raises(ValueError, match="fields"):
            read_policy(
                json_bytes(
                    {
                        "schema_version": version,
                        "files": {
                            "templates.json": {
                                "policy": "fixture",
                                "rules": "template.fixture-rules.json",
                            }
                        },
                        "new_files": {},
                    }
                )
            )


def test_cli_preview_public_validation_and_operator_stdio_surface(tmp_path, capsys):
    workspace = project(tmp_path)
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "fixture-preview",
                "templates.json",
                "--rules",
                "templates.fixture-rules.json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["ok"]
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "view-export",
                "policy.json",
                "--output",
                "cli-view",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--root",
                str(tmp_path / "cli-view"),
                "fixture-validate",
                "templates.json",
                "--contract",
                TEMPLATE_SCHEMA,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "valid"

    async def cycle():
        async with Client(
            create_server(tmp_path, Dictionary({"fuck": "assemble"}))
        ) as client:
            for name, args in [
                (
                    "preview_template_fixture",
                    {
                        "source": "templates.json",
                        "rules": "templates.fixture-rules.json",
                    },
                ),
                (
                    "export_project_view",
                    {"config": "policy.json", "output": "mcp-view"},
                ),
                (
                    "validate_template_fixture",
                    {
                        "source": "mcp-view/templates.json",
                        "contract": "mcp-view/" + TEMPLATE_SCHEMA,
                        "fixture": "templates.json",
                    },
                ),
            ]:
                result = await client.call_tool(name, args)
                assert not result.is_error
                content = result.content[0]
                assert content.type == "text"
                report = json.loads(content.text)
                assert report.get("ok", True)

    asyncio.run(cycle())
    assert workspace.read("templates.json").startswith(b"\xef\xbb\xbf")


def test_export_preserves_partial_publication(tmp_path, monkeypatch):
    workspace = project(tmp_path)
    real_write = project_views.atomic_write

    def fail_contract(path, data, **kwargs):
        if path.name == TEMPLATE_SCHEMA:
            raise OSError("injected fixture contract publication failure")
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(project_views, "atomic_write", fail_contract)
    with pytest.raises(OperationError) as error:
        export_view(workspace, "policy.json", "neutral")
    assert (
        error.value.report["status"] == "error"
        and not error.value.report["manifest_saved"]
    )
    assert (tmp_path / "neutral/templates.json").exists()
    assert list((tmp_path / ".local/bamboozle/views").glob("*/blobs/*.bin"))


@pytest.mark.parametrize("recover", [False, True])
def test_complete_generated_catalog_return_execution_and_explicit_import_recovery(
    tmp_path, monkeypatch, recover
):
    workspace, exported, view = export(tmp_path)
    original = workspace.read("templates.json")
    SCENARIO["write_agent_return"](view.root)

    def execute(root):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "src/run_catalog.py",
                str(view.root / TEMPLATE_CASES),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        assert result.stdout.strip() == "catalog-pipeline-ok"

    execute(view.root)
    fixture = parse_json(view.read("templates.json"))
    fixture["templates"][0]["prompt"] = fixture["templates"][0]["prompt"].replace(
        "Operator", "Technician"
    )
    (view.root / "templates.json").write_bytes(json_bytes(fixture))
    review = review_import(workspace, exported["view_id"])
    assert review["ok"]
    assert any(change["action"] == "fixture_ignored" for change in review["changes"])
    if recover:
        real_write = view_import.atomic_write

        def fail_after_first(path, data, **kwargs):
            if path == tmp_path / "src/run_catalog.py":
                raise OSError("injected returned source interruption")
            return real_write(path, data, **kwargs)

        monkeypatch.setattr(view_import, "atomic_write", fail_after_first)
        with pytest.raises(OperationError):
            apply_import(workspace, exported["view_id"], review["review_sha256"])
        assert (tmp_path / "src/catalog.py").exists()
        monkeypatch.setattr(view_import, "atomic_write", real_write)
    applied = apply_import(
        workspace, exported["view_id"], review["review_sha256"], recover=recover
    )
    assert applied["receipt_saved"]
    assert "from renderer import fuck" in workspace.read("src/catalog.py").decode()
    assert workspace.read("templates.json") == original
    assert all(not (tmp_path / name).exists() for name in GENERATED_FILES)
    execute(tmp_path)
    assert view_status(workspace, exported["view_id"])["status"] == "complete"


@pytest.mark.parametrize("mutation", ["delete", "invalid", "malformed"])
def test_invalid_generated_fixture_cannot_qualify_returned_code(tmp_path, mutation):
    workspace, exported, view = export(tmp_path)
    path = view.root / "templates.json"
    if mutation == "delete":
        path.unlink()
    elif mutation == "malformed":
        path.write_bytes(b"bad JSON")
    else:
        value = parse_json(path.read_bytes())
        value["templates"][0]["links"] = ["absent"]
        path.write_bytes(json_bytes(value))
    report = review_import(workspace, exported["view_id"])
    assert not report["ok"] and report["review_sha256"] is None
    assert report["conflicts"][0]["path"] == "templates.json"


def test_shared_rule_file_cannot_change_between_two_fixture_preparations(
    tmp_path, monkeypatch
):
    workspace = project(tmp_path)
    (tmp_path / "second.json").write_bytes(workspace.read("templates.json"))
    policy = parse_json(workspace.read("policy.json"))
    policy["files"]["second.json"] = {
        "policy": "fixture",
        "rules": "templates.fixture-rules.json",
    }
    (tmp_path / "policy.json").write_bytes(json_bytes(policy))
    actual = project_views.generate_fixture
    calls = 0

    def mutate_rule(*args, **kwargs):
        nonlocal calls
        result = actual(*args, **kwargs)
        calls += 1
        if calls == 1:
            rules = rules_document()
            rules["text"]["/prompt"]["template"] = (
                "Updated " + rules["text"]["/prompt"]["template"]
            )
            (tmp_path / "templates.fixture-rules.json").write_bytes(json_bytes(rules))
        return result

    monkeypatch.setattr(project_views, "generate_fixture", mutate_rule)
    with pytest.raises(ValueError, match="changed"):
        export_view(workspace, "policy.json", "neutral")
    assert not (tmp_path / "neutral").exists()


def test_consumer_cannot_change_after_binding_but_before_rendering(
    tmp_path, monkeypatch
):
    workspace = project(tmp_path)
    actual = project_views.generate_fixture

    def mutate_consumer(*args, **kwargs):
        result = actual(*args, **kwargs)
        path = tmp_path / "src/renderer.py"
        path.write_bytes(path.read_bytes() + b"# changed consumer\n")
        return result

    monkeypatch.setattr(project_views, "generate_fixture", mutate_consumer)
    with pytest.raises(ValueError, match="changed"):
        export_view(workspace, "policy.json", "neutral")
    assert not (tmp_path / "neutral").exists()


def test_shared_supplied_fixture_is_also_pinned_without_changing_old_policy_semantics(
    tmp_path, monkeypatch
):
    for name in ("first.txt", "second.txt", "neutral.txt"):
        (tmp_path / name).write_bytes(b"original fixture bytes")
    policy = {
        "schema_version": 1,
        "files": {
            name: {"policy": "fixture", "fixture": "neutral.txt"}
            for name in ("first.txt", "second.txt")
        },
        "new_files": {},
    }
    (tmp_path / "policy.json").write_bytes(json_bytes(policy))
    original_read = Workspace.read
    reads = 0

    def mutate(workspace, name):
        nonlocal reads
        data = original_read(workspace, name)
        if workspace.resolve(name).name == "neutral.txt":
            reads += 1
            if reads == 1:
                (tmp_path / "neutral.txt").write_bytes(b"changed fixture bytes")
        return data

    monkeypatch.setattr(Workspace, "read", mutate)
    with pytest.raises(ValueError, match="Shared supplied fixture changed"):
        export_view(Workspace(tmp_path), "policy.json", "neutral-view")
    assert not (tmp_path / "neutral-view").exists()


def test_record_specific_templates_and_root_array_collections(tmp_path):
    workspace = project(tmp_path)
    document = source_document()["templates"]
    document[1]["prompt"] = document[1]["prompt"].replace("actor_b!s", "actor_b!r")
    rules = rules_document()
    rules.update(records="", document_preserve=[])
    rules["text"]["/prompt"]["variants"] = {
        "template_018": rules["text"]["/prompt"]["template"].replace(
            "actor_b!s", "actor_b!r"
        )
    }
    (tmp_path / "templates.json").write_bytes(json_bytes(document))
    (tmp_path / "templates.fixture-rules.json").write_bytes(json_bytes(rules))
    prepared, _ = prepare_fixture(
        workspace, "templates.json", "templates.fixture-rules.json"
    )
    assert isinstance(parse_json(prepared.data), list)
    assert "actor_b!r" in parse_json(prepared.data)[1]["prompt"]


def test_diagnostics_are_bounded_without_losing_the_error_count(tmp_path):
    workspace = project(tmp_path)
    document = source_document()
    prototype = document["templates"][0]
    document["templates"] = [
        {
            **copy.deepcopy(prototype),
            "id": f"template_{i:03d}",
            "unclassified": "private",
        }
        for i in range(150)
    ]
    (tmp_path / "templates.json").write_bytes(json_bytes(document))
    report = preview_fixture(
        workspace, "templates.json", "templates.fixture-rules.json"
    )
    assert report["error_count"] == 150 and len(report["diagnostics"]) == 100
    assert not report["diagnostics_complete"] and report["codes"] == ["@unclassified"]


@pytest.mark.parametrize(
    "kind", ["records", "cases", "text", "bytes", "depth", "nonfinite"]
)
def test_generation_has_enforced_resource_bounds(tmp_path, kind):
    workspace = project(tmp_path)
    document, rules = source_document(), rules_document()
    if kind == "records":
        document["templates"] *= 129
    elif kind == "cases":
        rules["cases"] *= 5
    elif kind == "text":
        rules["text"]["/prompt"]["template"] = "x" * 65537
    elif kind == "bytes":
        workspace = Workspace(tmp_path, max_bytes=64)
    elif kind == "depth":
        for _ in range(65):
            document = {"nested": document}
    elif kind == "nonfinite":
        (tmp_path / "templates.json").write_bytes(b'{"templates":[],"number":1e999}')
    if kind != "nonfinite":
        (tmp_path / "templates.json").write_bytes(json_bytes(document))
    (tmp_path / "templates.fixture-rules.json").write_bytes(json_bytes(rules))
    with pytest.raises(ValueError):
        prepare_fixture(workspace, "templates.json", "templates.fixture-rules.json")
