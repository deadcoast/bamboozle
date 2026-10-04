"""Complete synthetic catalog scenario for source and installed-package qualification."""

import copy
from pathlib import Path
from textwrap import dedent


def write_agent_return(root: Path) -> None:
    """A complete returned catalog implementation with a separate integration runner."""
    renderer = """
        def assemble(record, values):
            return record["prompt"].format_map(values)
    """
    catalog = """
        from math import isfinite
        from string import Formatter
        from renderer import assemble

        class CatalogError(ValueError):
            def __init__(self, codes):
                self.codes = sorted(set(codes))
                super().__init__(", ".join(self.codes))

        def compile_catalog(document, values):
            records = document.get("templates")
            if not isinstance(records, list) or not records:
                raise CatalogError(["@records"])
            ids = [record.get("id") for record in records]
            if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
                raise CatalogError(["@id"])
            errors = []
            for record in records:
                version = record.get("version")
                if type(version) is not int or version < 1:
                    errors.append("version")
                duration = record.get("duration")
                if type(duration) not in (int, float) or not isfinite(duration) or not 0 <= duration <= 60:
                    errors.append("duration")
                constraints = record.get("constraints", {})
                roles = record.get("roles")
                if not isinstance(roles, list) or type(constraints.get("participant_count")) is not int or len(roles) != constraints["participant_count"]:
                    errors.append("roles")
                if constraints.get("ordered") is True and "role_order" not in record:
                    errors.append("ordered")
                if record.get("category") not in ("calibration", "inspection"):
                    errors.append("category")
                declared = record.get("variables")
                try:
                    fields = {field for _, field, _, _ in Formatter().parse(record["prompt"]) if field is not None}
                    if not isinstance(declared, list) or any(not isinstance(value, str) for value in declared) or len(set(declared)) != len(declared) or set(declared) != fields:
                        errors.append("variables")
                except (KeyError, ValueError, TypeError):
                    errors.append("variables")
                links = record.get("links")
                if not isinstance(links, list) or any(link not in ids for link in links):
                    errors.append("@reference")
            if errors:
                raise CatalogError(errors)
            result = []
            for record in records:
                bindings = {**values, "duration": record["duration"]}
                missing = set(record["variables"]) - bindings.keys()
                if missing:
                    raise CatalogError(["bindings"])
                result.append({"id": record["id"], "text": assemble(record, bindings), "links": record["links"]})
            return result
    """
    runner = """
        import json
        import sys
        from pathlib import Path
        from catalog import CatalogError, compile_catalog

        cases = json.loads(Path(sys.argv[1]).read_bytes())["fixtures"]["templates.json"]
        bindings = {"actor_a": "Ada", "actor_b": "Lin"}
        for case in cases:
            try:
                result = compile_catalog(case["document"], bindings)
            except CatalogError as error:
                assert error.codes == case["expect"], (case["name"], error.codes, case["expect"])
            else:
                assert not case["expect"], case["name"]
                assert [row["id"] for row in result] == ["template_017", "template_018"]
                assert result[0]["text"].count("Ada") == 2
                assert "Lin" in result[0]["text"] and "{station}" in result[0]["text"]
                assert result[0]["links"] == ["template_018"]
        production = json.loads(Path("templates.json").read_bytes())
        assert len(compile_catalog(production, bindings)) == 2
        try:
            compile_catalog(production, {"actor_a": "Ada"})
        except CatalogError as error:
            assert error.codes == ["bindings"]
        else:
            raise AssertionError("Missing runtime binding accepted")
        print("catalog-pipeline-ok")
    """
    for name, code in {
        "renderer.py": renderer,
        "catalog.py": catalog,
        "run_catalog.py": runner,
    }.items():
        (root / "src" / name).write_text(dedent(code).lstrip(), encoding="utf-8")


def source_document():
    record = {
        "id": "template_017",
        "category": "calibration",
        "version": 1,
        "variables": ["actor_a", "actor_b", "duration"],
        "prompt": "PRIVATE_SOURCE {actor_a} {actor_b!s} {duration:.1f} {{station}}.\nRepeat {actor_a}.",
        "roles": ["actor_a", "actor_b"],
        "duration": 2.5,
        "constraints": {"participant_count": 2, "ordered": True},
        "role_order": ["actor_a", "actor_b"],
        "links": ["template_018"],
    }
    second = {
        **copy.deepcopy(record),
        "id": "template_018",
        "links": [],
        "notes": "PRIVATE_NOTE",
    }
    return {"label": "calibration catalog", "templates": [record, second]}


def rules_document():
    return {
        "schema_version": 1,
        "records": "/templates",
        "id": "/id",
        "text": {
            "/prompt": {
                "template": "Operator {actor_a} transfers a calibration probe to {actor_b!s} for {duration:.1f} seconds at {{station}}.\nConfirm {actor_a}.",
                "syntax": "format",
                "optional": False,
                "variants": {},
            },
            "/notes": {
                "template": "Use the calibration bench.",
                "syntax": "none",
                "optional": True,
                "variants": {},
            },
        },
        "preserve": [
            "/id",
            "/category",
            "/variables/*",
            "/roles/*",
            "/role_order/*",
            "/links/*",
        ],
        "document_preserve": ["/label"],
        "checks": [
            {
                "id": "version",
                "kind": "type",
                "path": "/version",
                "type": "integer",
                "required": True,
                "minimum": 1,
            },
            {
                "id": "duration",
                "kind": "type",
                "path": "/duration",
                "type": "number",
                "required": True,
                "minimum": 0,
                "maximum": 60,
            },
            {
                "id": "roles",
                "kind": "count",
                "array": "/roles",
                "count": "/constraints/participant_count",
            },
            {
                "id": "variables",
                "kind": "variables",
                "array": "/variables",
                "text": "/prompt",
            },
            {
                "id": "ordered",
                "kind": "requires",
                "when": "/constraints/ordered",
                "equals": True,
                "then": ["/role_order"],
            },
            {
                "id": "category",
                "kind": "type",
                "path": "/category",
                "type": "string",
                "required": True,
                "enum": ["calibration", "inspection"],
                "min_length": 1,
            },
        ],
        "references": [{"path": "/links", "many": True, "optional": False}],
        "consumers": [
            {
                "path": "src/renderer.py",
                "symbol": "fuck",
                "fields": ["/prompt", "/variables"],
                "purpose": "Interpolate each record's named variables with its declared format specifiers.",
            }
        ],
        "literal_dependencies": [],
        "cases": [
            {
                "name": "zero_duration",
                "record": "template_017",
                "set": {"/duration": 0},
                "remove": [],
                "expect": [],
            },
            {
                "name": "missing_duration",
                "record": "template_017",
                "set": {},
                "remove": ["/duration"],
                "expect": ["duration"],
            },
            {
                "name": "wrong_variables",
                "record": "template_017",
                "set": {"/variables": ["actor_a"]},
                "remove": [],
                "expect": ["variables"],
            },
            {
                "name": "role_count",
                "record": "template_017",
                "set": {"/roles": ["actor_a"]},
                "remove": [],
                "expect": ["roles"],
            },
            {
                "name": "ordered_roles",
                "record": "template_017",
                "set": {},
                "remove": ["/role_order"],
                "expect": ["ordered"],
            },
            {
                "name": "dangling_reference",
                "record": "template_017",
                "set": {"/links": ["unknown"]},
                "remove": [],
                "expect": ["@reference"],
            },
            {
                "name": "boolean_version",
                "record": "template_017",
                "set": {"/version": True},
                "remove": [],
                "expect": ["version"],
            },
        ],
    }
