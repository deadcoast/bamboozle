"""Generate neutral JSON template fixtures with explicit, executable contracts.

This module owns product data preparation, not agent orchestration. It preserves
unselected data, verifies named invariants, and never executes consumer source.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from typing import Any

from bamboozle.agent_files import source_path
from bamboozle.dictionary import Dictionary
from bamboozle.engine import Translator
from bamboozle.fixture_rules import (
    MAX_RECORDS,
    MISSING,
    Findings,
    _json,
    _replace,
    _selected,
    _walk,
    at,
    format_signature,
    pointer,
    read_rules,
    validate_data,
)
from bamboozle.markers import CURRENT
from bamboozle.utils import MAX_FILE_BYTES, json_bytes, sha256
from bamboozle.workspace import Workspace

TEMPLATE_CONTRACT = "TEMPLATE_CONTRACT.md"
TEMPLATE_SCHEMA = "TEMPLATE_CONTRACT.json"
TEMPLATE_CASES = "TEMPLATE_CASES.json"
GENERATED_FILES = (TEMPLATE_CONTRACT, TEMPLATE_SCHEMA, TEMPLATE_CASES)


def inspect_consumers(
    rules: dict[str, Any],
    sources: dict[str, bytes],
    mapping: Dictionary | None = None,
    translated: set[str] | None = None,
    *,
    marker_version: int = CURRENT,
    identifier_maps: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    consumers = []
    for consumer in rules["consumers"]:
        name = consumer["path"]
        if name not in sources or not name.endswith(".py"):
            raise ValueError(
                "Each declared consumer needs a selected Python source file"
            )
        try:
            tree = ast.parse(sources[name].decode("utf-8-sig"), filename=name)
        except (SyntaxError, RecursionError) as exc:
            raise ValueError(f"Consumer Python cannot be parsed: {name}") from exc
        node: Any = tree
        for part in consumer["symbol"].split("."):
            matches = [
                child
                for child in node.body
                if isinstance(
                    child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                )
                and child.name == part
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"Consumer symbol is absent or ambiguous: {name}:{consumer['symbol']}"
                )
            node = matches[0]
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            raise TypeError(
                "Consumer symbols must resolve to callable function definitions"
            )
        symbol: str = consumer["symbol"]
        if identifier_maps is not None and name in identifier_maps:
            selected_names = identifier_maps[name]
            symbol = ".".join(
                selected_names.get(part, part) for part in symbol.split(".")
            )
        elif mapping is not None and name in (translated or set()):
            symbol = (
                Translator(mapping, marker_version=marker_version)
                .transform(symbol)
                .text
            )
        consumers.append({**consumer, "symbol": symbol, "line": node.lineno})
    return consumers


@dataclass(frozen=True)
class Fixture:
    data: bytes
    contract: dict[str, Any]
    cases: list[dict[str, Any]]


def generate_fixture(
    raw: bytes,
    rules: dict[str, Any],
    consumers: list[dict[str, Any]],
    *,
    limit: int = MAX_FILE_BYTES,
) -> Fixture:
    if rules["literal_dependencies"]:
        raise ValueError(
            "Unresolved literal-prose dependencies: expose their structured fields or keep that source explicitly visible; generation is refused"
        )
    source = _json(raw, limit)
    checked = validate_data(source, rules)
    if not checked["ok"]:
        raise FixtureError(
            "Production records violate declared fixture invariants", checked
        )
    records = at(source, rules["records"])
    for consumer in consumers:
        if any(
            not any(at(record, path) is not MISSING for record in records)
            for path in consumer["fields"]
        ):
            raise ValueError(
                "Declared consumer field does not occur in any production record"
            )
    ids = {at(record, rules["id"]) for record in records}
    for rule in rules["text"].values():
        if set(rule["variants"]) - ids:
            raise ValueError("Neutral text variants name unknown record IDs")
    result = copy.deepcopy(source)
    prepared = at(result, rules["records"])
    signatures = []
    string_selectors = [pointer(path, wildcards=True) for path in rules["preserve"]]
    replaced = {pointer(path) for path in rules["text"]}
    findings = Findings()
    for index, (record, target) in enumerate(zip(records, prepared, strict=True)):
        identifier = at(record, rules["id"])
        signatures.append({"id": identifier, "fields": {}})
        for path, value in _walk(record):
            if (
                isinstance(value, str)
                and path not in replaced
                and not _selected(path, string_selectors, record)
            ):
                findings.add(
                    "@unclassified",
                    rules["records"]
                    + f"/{index}/"
                    + "/".join(
                        part.replace("~", "~0").replace("/", "~1") for part in path
                    ),
                    "Classify this string as replaced text or explicitly preserved technical data",
                )
        for path, rule in rules["text"].items():
            value = at(record, path)
            if value is MISSING:
                continue
            neutral = rule["variants"].get(identifier, rule["template"])
            signature = format_signature(value, rule["syntax"])
            signatures[-1]["fields"][path] = signature
            if format_signature(neutral, rule["syntax"]) != signature:
                findings.add(
                    "@format",
                    rules["records"] + f"/{index}" + path,
                    "Neutral template must preserve placeholders/specifiers/multiplicity, escaped braces and exact newline sequence",
                )
            _replace(target, path, neutral)
    document_selectors = [
        pointer(path, wildcards=True) for path in rules["document_preserve"]
    ]
    record_prefix = pointer(rules["records"])
    for path, value in _walk(source):
        if (
            path[: len(record_prefix)] != record_prefix
            and isinstance(value, str)
            and not _selected(path, document_selectors, source)
        ):
            findings.add(
                "@unclassified",
                "/" + "/".join(path),
                "Classify document-level string metadata explicitly",
            )
    if findings.count:
        raise FixtureError(
            "Fixture generation needs explicit invariant-preserving rules",
            findings.report(),
        )
    public_rules = {
        **rules,
        "consumers": [
            {key: item[key] for key in ("path", "symbol", "fields", "purpose")}
            for item in consumers
        ],
        "cases": [],
    }
    contract = {"rules": public_rules, "signatures": signatures, "consumers": consumers}
    baseline = validate_data(result, rules, signatures=signatures)
    if not baseline["ok"]:
        raise FixtureError("Generated fixture violates its contract", baseline)
    cases = [{"name": "baseline", "expect": [], "document": result}]
    size = len(json_bytes(cases[0]))
    for case in rules["cases"]:
        document = copy.deepcopy(result)
        candidates = [
            record
            for record in at(document, rules["records"])
            if at(record, rules["id"]) == case["record"]
        ]
        if len(candidates) != 1:
            raise ValueError("Case record must name an existing stable ID")
        for path, value in case["set"].items():
            _replace(candidates[0], path, copy.deepcopy(value))
        for path in case["remove"]:
            _replace(candidates[0], path)
        checked = validate_data(document, rules, signatures=signatures)
        if set(checked["codes"]) != set(case["expect"]):
            raise FixtureError(
                f"Case {case['name']} does not produce exactly its declared diagnostics",
                checked,
            )
        rendered = {
            "name": case["name"],
            "expect": sorted(set(case["expect"])),
            "document": document,
        }
        size += len(json_bytes(rendered))
        if size > limit:
            raise ValueError("Generated case corpus exceeds the file limit")
        cases.append(rendered)
    data = json_bytes(result)
    if max(len(data), len(json_bytes(contract)), len(json_bytes(cases))) > limit:
        raise ValueError("Generated fixture/contract/cases exceed the file limit")
    return Fixture(data, contract, cases)


class FixtureError(ValueError):
    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = {**report, "ok": False, "status": "invalid", "error": message}


def artifacts(fixtures: dict[str, Fixture]) -> dict[str, bytes]:
    contracts = {name: item.contract for name, item in sorted(fixtures.items())}
    cases = {name: item.cases for name, item in sorted(fixtures.items())}
    lines = [
        "# Template processing contract",
        "",
        "These neutral records preserve declared software invariants, not production scene semantics.",
        "Fixture edits and cases must never replace production template text.",
        "Generated contract/case files must remain unchanged; create separate test files for new cases.",
        "",
        "Validate a fixture with `bamboozle fixture-validate PATH --contract TEMPLATE_CONTRACT.json`.",
        "`TEMPLATE_CASES.json` supplies complete documents and expected invariant codes; an empty list means valid.",
        "Consumer symbols and field responsibilities are operator-declared and checked against parsed Python source, not inferred behavior.",
        "",
    ]
    for name, fixture in sorted(fixtures.items()):
        rule = fixture.contract["rules"]
        lines += [
            f"## {name}",
            "",
            f"Records: `{rule['records']}`; stable ID: `{rule['id']}`.",
            "",
            "### Field definitions and checks",
            "",
            "```json",
            json_bytes(
                {
                    key: rule[key]
                    for key in (
                        "text",
                        "preserve",
                        "document_preserve",
                        "checks",
                        "references",
                    )
                }
            )
            .decode()
            .rstrip(),
            "```",
            "",
            "### Processing call paths",
            "",
        ]
        for consumer in fixture.contract["consumers"]:
            lines.append(
                f"- `{consumer['path']}:{consumer['line']}` / `{consumer['symbol']}`: {consumer['purpose']} Fields: {json_bytes(consumer['fields']).decode().strip()}"
            )
        lines += [
            "",
            "### Verified cases",
            "",
            *[
                f"- `{case['name']}`: {', '.join(case['expect']) or 'valid'}"
                for case in fixture.cases
            ],
            "",
        ]
    return {
        TEMPLATE_SCHEMA: json_bytes({"schema_version": 1, "fixtures": contracts}),
        TEMPLATE_CASES: json_bytes({"schema_version": 1, "fixtures": cases}),
        TEMPLATE_CONTRACT: "\n".join(lines).encode("utf-8"),
    }


def prepare_fixture(
    workspace: Workspace, source: str, rules_path: str
) -> tuple[Fixture, dict[str, str]]:
    config = workspace.resolve(rules_path)
    if not config.name.casefold().endswith(".fixture-rules.json"):
        raise ValueError("Use a private .fixture-rules.json rule file")
    raw_rules = workspace.read(config)
    rules = read_rules(raw_rules)
    original = source_path(workspace, source, private_paths={config})
    raw = workspace.read(original)
    inputs = {source: sha256(raw), rules_path: sha256(raw_rules)}
    sources = {}
    for consumer in rules["consumers"]:
        path = source_path(workspace, consumer["path"], private_paths={config})
        sources[consumer["path"]] = workspace.read(path)
        inputs[consumer["path"]] = sha256(sources[consumer["path"]])
    result = generate_fixture(
        raw, rules, inspect_consumers(rules, sources), limit=workspace.max_bytes
    )
    return result, inputs


def preview_fixture(workspace: Workspace, source: str, rules: str) -> dict[str, Any]:
    try:
        result, inputs = prepare_fixture(workspace, source, rules)
    except FixtureError as exc:
        return exc.report
    public = artifacts({source: result})
    digest = sha256(
        json_bytes(
            {
                "inputs": inputs,
                "fixture": sha256(result.data),
                "artifacts": {name: sha256(data) for name, data in public.items()},
            }
        )
    )
    return {
        "ok": True,
        "status": "preview",
        "generation_sha256": digest,
        "inputs": inputs,
        "fixture_sha256": sha256(result.data),
        "fixture_preview": result.data[:32768].decode("utf-8", errors="replace"),
        "preview_complete": len(result.data) <= 32768,
        "contract_preview": public[TEMPLATE_CONTRACT][:32768].decode(
            "utf-8", errors="replace"
        ),
        "contract_complete": len(public[TEMPLATE_CONTRACT]) <= 32768,
        "cases": [
            {"name": case["name"], "expect": case["expect"]} for case in result.cases
        ],
        "output_saved": False,
    }


def validate_fixture(
    workspace: Workspace, source: str, contract: str, *, fixture: str | None = None
) -> dict[str, Any]:
    document = _json(workspace.read(contract), workspace.max_bytes)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "fixtures"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or not isinstance(document["fixtures"], dict)
        or not 1 <= len(document["fixtures"]) <= 512
    ):
        raise ValueError("Invalid public fixture contract")
    name = fixture or source
    selected = document["fixtures"].get(name)
    report = validate_contract(
        workspace.read(source), selected, limit=workspace.max_bytes
    )
    return {**report, "status": "valid" if report["ok"] else "invalid", "fixture": name}


def validate_contract(
    raw: bytes, selected: Any, *, limit: int = MAX_FILE_BYTES
) -> dict[str, Any]:
    """Validate bytes against one retained/public fixture contract."""
    if not isinstance(selected, dict) or set(selected) != {
        "rules",
        "signatures",
        "consumers",
    }:
        raise ValueError("Select a fixture path present in the public contract")
    rules = read_rules(json_bytes(selected["rules"]))
    signatures = selected["signatures"]
    if (
        not isinstance(signatures, list)
        or not 1 <= len(signatures) <= MAX_RECORDS
        or any(
            not isinstance(item, dict)
            or set(item) != {"id", "fields"}
            or not isinstance(item["id"], str)
            or not isinstance(item["fields"], dict)
            for item in signatures
        )
    ):
        raise ValueError("Invalid saved fixture signatures")
    return validate_data(_json(raw, limit), rules, signatures=signatures)
