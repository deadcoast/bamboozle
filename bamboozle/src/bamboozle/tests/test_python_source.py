"""Executable Python adapters, explicit bindings and complete reviewed source return."""

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from bamboozle import service, view_import
from bamboozle.dictionary import Dictionary
from bamboozle.formats import DocumentCodec, FormatRules, format_options
from bamboozle.project_views import CONTRACT, export_view, load_view
from bamboozle.python_project import validate_project
from bamboozle.python_source import MAX_CHARS
from bamboozle.restoration import prepare_restore, verify_restoration
from bamboozle.return_validation import inspect_return, validate_return
from bamboozle.utils import atomic_write, json_bytes
from bamboozle.workspace import Workspace

MAPPING = Dictionary(
    {
        "fuck": "assemble",
        "fuck_count": "item_count",
        "worker": "processor",
        "alpha": "omega",
    }
)


@pytest.mark.parametrize(
    "source",
    [
        "import types\nobj = types.SimpleNamespace(fuck=1)\ndef f(obj):\n return obj.fuck\n",
        "import types\nobj = types.SimpleNamespace(fuck=1)\nobj = ProjectObject()\nprint(obj.fuck)\n",
        'from builtins import getattr as lookup\nlookup(obj, "fuck")\n',
        'fetch = getattr\nfetch(obj, "fuck")\n',
    ],
)
def test_ambiguous_external_ownership_and_aliased_reflection_refuse(source):
    with pytest.raises(
        ValueError, match="import_shadow|unselected_reference|dynamic_namespace"
    ):
        codec().transform(source)


def test_explicit_builtin_reflection_alias_with_selected_literal_executes():
    source = 'from builtins import getattr as lookup\nclass Worker:\n def fuck(self):\n  return 7\nassert lookup(Worker(), "fuck")() == 7\n'
    selected = codec(strings=("fuck",))
    result = selected.transform(source)
    execute(result.text)
    assert (
        selected.transform(result.text, "reverse", context=result.context).text
        == source
    )


@pytest.mark.parametrize("target", ["__private", "__len__"])
def test_dictionary_cannot_introduce_name_mangling_or_magic_methods(target):
    with pytest.raises(ValueError, match="target must"):
        DocumentCodec(
            Dictionary({"field": target}), FormatRules("python", identifiers=("field",))
        )


def codec(names=("fuck",), modules=(), strings=()):
    return DocumentCodec(
        MAPPING,
        FormatRules("python", identifiers=names, modules=modules, strings=strings),
    )


def execute(text):
    scope = {}
    exec(compile(text.removeprefix("\ufeff"), "scenario.py", "exec"), scope)  # noqa: S102 - trusted synthetic programs
    return scope


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("bom", ["", "\ufeff"])
def test_spans_preserve_unselected_syntax_bytes_and_execute(bom, newline):
    source = (
        bom
        + '''# fuck __SCM_CASE_ is literal documentation
PATH = r"C:\\models\\fuck.bin"
URL = "https://example.test/fuck"
PROSE = "fuck assemble __SCM_LIT_bad__"
def fuck(fuck_count=2):
    """fuck is an identifier; this docstring stays byte exact."""
    return fuck_count ** 2
answer = fuck(fuck_count=3)
rendered = f"literal fuck: {fuck(fuck_count=4)}"
assert answer == 9 and rendered == "literal fuck: 16"
'''.replace("\n", newline)
    )
    selected = codec(("fuck", "fuck_count"))
    encoded = selected.transform(source)
    assert "def assemble(item_count=2)" in encoded.text
    assert "C:\\models\\fuck.bin" in encoded.text
    assert '"fuck assemble __SCM_LIT_bad__"' in encoded.text
    assert (
        selected.transform(encoded.text, "reverse", context=encoded.context).text
        == source
    )
    assert execute(source)["answer"] == execute(encoded.text)["answer"] == 9


def test_closures_shadowing_global_nonlocal_comprehension_lambda_and_pattern_bindings():
    source = """fuck = 3
def outer(fuck):
    def update(step):
        nonlocal fuck
        fuck += step
        return fuck
    return update
def global_update():
    global fuck
    fuck += 1
    return fuck
change = outer(10)
assert change(2) == 12 and change(-1) == 11
assert global_update() == 4
assert [fuck * fuck for fuck in range(3)] == [0, 1, 4]
assert (lambda fuck: fuck + 1)(7) == 8
match {"x": 4}:
    case {"x": fuck} if fuck > 0:
        answer = fuck
assert answer == 4
"""
    selected = codec()
    encoded = selected.transform(source)
    execute(encoded.text)
    assert selected.transform(encoded.text, "reverse").text == source


def test_external_import_members_attributes_and_keywords_are_preserved():
    source = """from math import factorial as fuck
import types as alpha
value = fuck(5)
thing = alpha.SimpleNamespace(fuck=8)
assert value == 120 and thing.fuck == 8
"""
    selected = codec(("fuck", "alpha"))
    encoded = selected.transform(source)
    assert "from math import factorial as assemble" in encoded.text
    assert "import types as omega" in encoded.text
    execute(encoded.text)
    assert selected.transform(encoded.text, "reverse").text == source


def test_external_module_attribute_and_keyword_names_are_not_translated():
    selected = DocumentCodec(
        Dictionary({"sqrt": "radical", "rel_tol": "relative"}),
        FormatRules("python", identifiers=("sqrt", "rel_tol")),
    )
    source = "import math\nsqrt = 4\nrel_tol = 0.001\nassert math.sqrt(sqrt) == 2\nassert math.isclose(2, 2, rel_tol=rel_tol)\n"
    encoded = selected.transform(source)
    assert "math.sqrt(radical)" in encoded.text
    assert "rel_tol=relative" in encoded.text
    execute(encoded.text)
    assert selected.transform(encoded.text, "reverse").text == source


def test_selected_symbol_strings_restore_each_original_token_spelling():
    source = """class Worker:
    __slots__ = (r"fuck_count",)
    def __init__(self, fuck_count):
        self.fuck_count = fuck_count
    def fuck(self):
        return self.fuck_count
__all__ = ['Worker']
def factory() -> "Worker":
    return Worker(**{"fuck_count": 7})
instance = factory()
assert getattr(instance, '\\x66uck')() == 7
assert hasattr(instance, "fuck_count")
"""
    selected = codec(
        ("Worker", "fuck", "fuck_count"), strings=("Worker", "fuck", "fuck_count")
    )
    encoded = selected.transform(source)
    assert "class Processor:" in encoded.text
    assert "'assemble'" in encoded.text and "\\x66" not in encoded.text
    execute(encoded.text)
    assert (
        selected.transform(encoded.text, "reverse", context=encoded.context).text
        == source
    )
    # A newly named program with no spelling context still decodes valid source.
    restored = selected.transform(encoded.text, "reverse")
    execute(restored.text)
    assert "'fuck'" in restored.text


@pytest.mark.parametrize(
    "source,code",
    [
        ("def fuck():\n pass\nassemble = 4\n", "name_collision"),
        (
            "def outer(assemble):\n def fuck():\n  return assemble\n return fuck\n",
            "name_collision",
        ),
        ("from external import fuck\nfuck()\n", "import_binding"),
        ("import fuck\nfuck.run()\n", "import_binding"),
        ("from external import *\n", "star_import"),
        ("getattr(obj, name)\n", "dynamic_member"),
        ('getattr(obj, "fuck")\n', "unselected_reference"),
        ('__all__ = ["fuck"]\n', "unselected_reference"),
        ('class C:\n __slots__ = ["fuck"]\n', "unselected_reference"),
        ('def f(x: "fuck"): pass\n', "unselected_reference"),
        ('function(**{"fuck": 2})\n', "unselected_reference"),
        ('eval("1+1")\n', "dynamic_namespace"),
        ("obj.__dict__[name]\n", "dynamic_namespace"),
        ("import math\ndef f(math):\n return math.fuck\n", "import_shadow"),
        ("def fuck(:\n", "syntax"),
        ("return 1\n", "syntax"),
    ],
)
def test_ambiguous_or_invalid_programs_refuse_before_publication(
    tmp_path, source, code
):
    (tmp_path / "input.py").write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match=code):
        service.prepare(
            Workspace(tmp_path),
            "input.py",
            dictionary=MAPPING,
            format_rules=codec().rules,
        )
    assert not (tmp_path / "input_safe.py").exists()
    assert not (tmp_path / ".input_safe.py.scm_ledger.json").exists()


def test_builtin_shadowing_is_distinguished_from_unbound_builtin_calls():
    selected = DocumentCodec(
        Dictionary({"len": "measure"}), FormatRules("python", identifiers=("len",))
    )
    with pytest.raises(ValueError, match="builtin_reference"):
        selected.transform("value = len([1,2])\n")
    original = "def f(len):\n return len + 1\nassert f(3) == 4\n"
    execute(selected.transform(original).text)
    with pytest.raises(ValueError, match="non-builtin"):
        DocumentCodec(
            Dictionary({"measure": "len"}),
            FormatRules("python", identifiers=("measure",)),
        )


@pytest.mark.parametrize(
    "rules",
    [
        {"format": "python"},
        {"format": "python", "identifiers": []},
        {"format": "python", "identifiers": ["def"]},
        {"format": "python", "identifiers": ["fuck", "fuck"]},
        {"format": "python", "identifiers": ["__dict__"]},
        {"format": "python", "identifiers": ["a-b"]},
        {"format": "python", "identifiers": ["fuck"], "modules": ["a/b"]},
        {"format": "python", "identifiers": ["fuck"], "strings": [True]},
        {"format": "python", "identifiers": ["fuck"], "unknown": False},
    ],
)
def test_invalid_selection_rules(rules):
    with pytest.raises((ValueError, TypeError)):
        FormatRules.from_document(rules)


def test_missing_explicit_mapping_and_incompatible_format_options():
    with pytest.raises(ValueError, match="exact dictionary"):
        codec(("fuck_missing",))
    with pytest.raises(ValueError, match="require --format python"):
        format_options(None, None, ["fuck"])
    with pytest.raises(ValueError, match="only to python"):
        format_options("text", None, ["fuck"])


def test_protected_string_corruption_is_literal_but_invalid_python_return_is_refused(
    tmp_path,
):
    source = 'def fuck(value):\n return value + 1\nDOC = "__SCM_LIT_broken"\nassert fuck(2) == 3\n'
    workspace = Workspace(tmp_path)
    (tmp_path / "source.py").write_text(source, encoding="utf-8")
    service.prepare(
        workspace, "source.py", dictionary=MAPPING, format_rules=codec().rules
    ).apply()
    assert (
        validate_return(workspace, "source_safe.py", "source.py")["status"]
        == "accepted"
    )
    output = tmp_path / "source_safe.py"
    output.write_text(
        output.read_text().replace("value + 1", "value + 2").replace("== 3", "== 4"),
        encoding="utf-8",
    )
    report = validate_return(workspace, "source_safe.py", "source.py")
    assert report["decodable"] and "protected_python_changed" in report["counts"]
    plan = prepare_restore(
        workspace,
        "source_safe.py",
        "source.py",
        terms="translate",
        output="restored.py",
    )
    plan.apply(plan.report()["review_sha256"])
    assert verify_restoration(workspace, "restored.py")["status"] == "verified"
    execute((tmp_path / "restored.py").read_text())
    output.write_text("def assemble(:\n", encoding="utf-8")
    assert (
        validate_return(workspace, "source_safe.py", "source.py")["status"] == "refused"
    )


@pytest.mark.parametrize("recover", [False, True])
def test_dependent_view_modules_retain_private_selections_and_recover(
    tmp_path, monkeypatch, recover
):
    workspace = Workspace(tmp_path)
    rules = codec(("fuck", "fuck_count"), modules=("core",)).rules.document()
    source = b"\xef\xbb\xbfdef fuck(fuck_count=2):\r\n    return fuck_count ** 2\r\n"
    (tmp_path / "core.py").write_bytes(source)
    (tmp_path / "policy.json").write_bytes(
        json_bytes(
            {
                "schema_version": 2,
                "files": {"core.py": {"policy": "translate", "document": rules}},
                "new_files": {".": {"policy": "translate", "document": rules}},
            }
        )
    )
    exported = export_view(workspace, "policy.json", "view", MAPPING)
    view = tmp_path / "view"
    contract = (view / CONTRACT).read_text()
    assert (
        "fuck" not in contract and "assemble" in contract and "item_count" in contract
    )
    (view / "core.py").write_bytes(
        (view / "core.py").read_bytes().replace(b"** 2", b"** 3")
    )
    (view / "main.py").write_text(
        "from core import assemble\nassert assemble(item_count=3) == 27\n",
        encoding="utf-8",
    )
    subprocess.run([sys.executable, str(view / "main.py")], check=True, timeout=10)
    reviewed = view_import.review_import(workspace, exported["view_id"])
    assert reviewed["ok"]
    if recover:

        def interrupt(path, data, **kwargs):
            if path == tmp_path / "main.py":
                raise OSError("retained source return interruption")
            return atomic_write(path, data, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(view_import, "atomic_write", interrupt)
            with pytest.raises(service.OperationError):
                view_import.apply_import(
                    workspace, exported["view_id"], reviewed["review_sha256"]
                )
    view_import.apply_import(
        workspace, exported["view_id"], reviewed["review_sha256"], recover=recover
    )
    subprocess.run([sys.executable, str(tmp_path / "main.py")], check=True, timeout=10)
    assert (tmp_path / "core.py").read_bytes() == source.replace(b"** 2", b"** 3")
    assert b"from core import fuck" in (tmp_path / "main.py").read_bytes()
    assert (
        view_import.view_status(workspace, exported["view_id"])["status"] == "complete"
    )


def test_private_string_context_is_validated_before_use():
    selected = codec(strings=("fuck",))
    result = selected.transform('value = "fuck"\n')
    for mutation in ("encoded", "original"):
        corrupted = json.loads(json_bytes(result.context))
        corrupted["strings"][0][mutation] = repr("arbitrary payload")
        with pytest.raises(ValueError, match="context"):
            selected.transform(result.text, "reverse", context=corrupted)


def test_unselected_dict_terms_are_not_treated_as_selected_python_vocabulary():
    selected = codec()
    source = 'alpha = "fuck"\ndef fuck():\n return alpha\n'
    result = selected.transform(source)
    assert (
        inspect_return(
            result.text.encode(),
            MAPPING,
            rules=selected.rules,
            baseline=result.text.encode(),
        )["status"]
        == "accepted"
    )
    assert selected.transform(result.text, "reverse").text == source


@pytest.mark.parametrize(
    "source,strings,message",
    [
        ('x = "fuck" " more"\n', ("fuck",), "concatenated_literal"),
        ('x = "fu" "ck"\n', ("fuck",), "concatenated_literal"),
        ('x = "fuck"\ny = "assemble"\n', ("fuck",), "string_collision"),
        ("# coding: latin1\nx = 1\n", (), "UTF-8"),
        ("ａｓｓｅｍｂｌｅ = 2\ndef fuck(): pass\n", (), "name_collision"),
    ],
)
def test_colliding_literals_encodings_and_normalized_identifiers_are_actionable(
    source, strings, message
):
    with pytest.raises(ValueError, match=message):
        codec(strings=strings).transform(source)


def test_mixed_case_and_unicode_offsets_in_fstring_expressions():
    selected = codec(("fUcK",))
    source = 'é = "untouched"\ndef fUcK(value):\n return value + 2\nanswer = f"{é}: {fUcK(3)}"\nassert answer == "untouched: 5"\n'
    encoded = selected.transform(source)
    assert "__SCM_CASE_" in encoded.text
    execute(encoded.text)
    assert selected.transform(encoded.text, "reverse").text == source


def test_python_input_and_output_limits_are_enforced():
    with pytest.raises(ValueError, match="analysis limit"):
        codec().transform("#" + "x" * MAX_CHARS)
    selected = DocumentCodec(MAPPING, codec().rules, limit=15)
    with pytest.raises(ValueError, match="byte limit"):
        selected.transform("fuck=1\nfuck=2\n")


def test_project_graph_validates_relative_imports_and_copied_provider_bindings():
    files = {
        "pkg/core.py": b"def assemble(): return 4\n",
        "pkg/main.py": b"from .core import assemble\nanswer=assemble()\n",
    }
    report = validate_project(files, {"pkg/main.py": (".core",)})
    assert (
        report["imported_members_checked"] == 1
        and report["relationships"][0]["provider"] == "pkg/core.py"
    )
    assert report["runtime_execution_required"]


@pytest.mark.parametrize(
    "provider,consumer,modules,message",
    [
        (b"def fuck(): pass\n", b"from core import assemble\n", ("core",), "not bound"),
        (
            b"def fuck(): pass\n",
            b"import core\ncore.assemble()\n",
            ("core",),
            "not bound",
        ),
        (
            b"def assemble(): pass\n",
            b"from missing import assemble\n",
            ("missing",),
            "missing",
        ),
        (
            b"def assemble(): pass\n",
            b"from ..core import assemble\n",
            ("..core",),
            "escapes",
        ),
    ],
)
def test_graph_refuses_broken_cross_module_contracts(
    provider, consumer, modules, message
):
    with pytest.raises(ValueError, match=message):
        validate_project(
            {"core.py": provider, "main.py": consumer}, {"main.py": modules}
        )


def test_graph_mismatch_on_return_has_no_apply_digest_or_source_writes(tmp_path):
    workspace = Workspace(tmp_path)
    source = b"def fuck(): return 4\n"
    (tmp_path / "core.py").write_bytes(source)
    rule = {
        "policy": "translate",
        "document": codec(modules=("core",)).rules.document(),
    }
    (tmp_path / "policy.json").write_bytes(
        json_bytes(
            {"schema_version": 2, "files": {"core.py": rule}, "new_files": {".": rule}}
        )
    )
    exported = export_view(workspace, "policy.json", "view", MAPPING)
    (tmp_path / "view/main.py").write_text(
        "from core import assemble_missing\n", encoding="utf-8"
    )
    review = view_import.review_import(workspace, exported["view_id"])
    assert not review["ok"] and review["review_sha256"] is None
    assert "not bound" in review["conflicts"][0]["reason"]
    assert (tmp_path / "core.py").read_bytes() == source and not (
        tmp_path / "main.py"
    ).exists()


def test_syntax_preserving_consumers_use_exact_selected_symbols_in_generated_fixture_contract(
    tmp_path,
):
    scenario = runpy.run_path(
        str(Path(__file__).resolve().parents[3] / "scripts/fixture_scenario.py")
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src/renderer.py").write_text(
        'class Worker:\n def fuck(self, record, values):\n  return record["prompt"].format_map(values)\n',
        encoding="utf-8",
    )
    (tmp_path / "templates.json").write_bytes(json_bytes(scenario["source_document"]()))
    rules = scenario["rules_document"]()
    rules["consumers"][0]["symbol"] = "Worker.fuck"
    (tmp_path / "templates.fixture-rules.json").write_bytes(json_bytes(rules))
    policy = {
        "schema_version": 3,
        "files": {
            "src/renderer.py": {
                "policy": "translate",
                "document": codec().rules.document(),
            },
            "templates.json": {
                "policy": "fixture",
                "rules": "templates.fixture-rules.json",
            },
        },
        "new_files": {},
    }
    (tmp_path / "policy.json").write_bytes(json_bytes(policy))
    workspace = Workspace(tmp_path)
    exported = export_view(workspace, "policy.json", "view", MAPPING)
    contract = json.loads((tmp_path / "view/TEMPLATE_CONTRACT.json").read_bytes())
    consumer = contract["fixtures"]["templates.json"]["consumers"][0]
    assert consumer["symbol"] == "Worker.assemble"
    source = (tmp_path / "view/src/renderer.py").read_text()
    namespace = execute(source)
    assert (
        namespace["Worker"]().assemble({"prompt": "Measure {n}"}, {"n": 3})
        == "Measure 3"
    )
    load_view(workspace, exported["view_id"])
    assert view_import.review_import(workspace, exported["view_id"])["ok"]
