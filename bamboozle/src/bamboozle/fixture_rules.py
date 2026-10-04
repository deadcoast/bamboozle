"""Strict fixture rules, JSON selection and invariant validation shared by all interfaces."""

from __future__ import annotations

import math
import re
import string
from collections import Counter
from typing import Any

from bamboozle.utils import MAX_FILE_BYTES, parse_json

MAX_RECORDS = 256
MAX_CASES = 32
MAX_DIAGNOSTICS = 100
MISSING = object()
NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}\Z")


def pointer(value: Any, *, wildcards: bool = False) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or (value and not value.startswith("/"))
        or re.search(r"~(?![01])", value)
    ):
        raise ValueError(
            "Fixture paths must be bounded JSON pointers with ~0/~1 escapes"
        )
    parts = tuple(
        part.replace("~1", "/").replace("~0", "~") for part in value.split("/")[1:]
    )
    if len(parts) > 64 or (not wildcards and "*" in parts):
        raise ValueError(
            "Wildcards are allowed only in explicit string-preservation selectors"
        )
    return parts


def at(document: Any, path: str) -> Any:
    value = document
    for part in pointer(path):
        if isinstance(value, dict):
            if part not in value:
                return MISSING
            value = value[part]
        elif (
            isinstance(value, list)
            and re.fullmatch(r"0|[1-9][0-9]*", part)
            and len(part) <= 6
            and int(part) < len(value)
        ):
            value = value[int(part)]
        else:
            return MISSING
    return value


def _replace(document: Any, path: str, value: Any = MISSING) -> None:
    parts = pointer(path)
    if not parts:
        raise ValueError("Fixture edits must name a field, not replace a record root")
    parent = document
    for part in parts[:-1]:
        if isinstance(parent, dict) and part in parent:
            parent = parent[part]
        elif (
            isinstance(parent, list)
            and re.fullmatch(r"0|[1-9][0-9]*", part)
            and len(part) <= 6
            and int(part) < len(parent)
        ):
            parent = parent[int(part)]
        else:
            raise ValueError(f"Fixture edit parent is absent: {path}")
    final = parts[-1]
    if (
        isinstance(parent, list)
        and re.fullmatch(r"0|[1-9][0-9]*", final)
        and len(final) <= 6
        and int(final) < len(parent)
    ):
        if value is MISSING:
            del parent[int(final)]
        else:
            parent[int(final)] = value
        return
    if not isinstance(parent, dict):
        raise TypeError("Fixture edits require an object field or existing array index")
    if value is MISSING:
        if final not in parent:
            raise ValueError(f"Fixture removal field is absent: {path}")
        del parent[final]
    else:
        parent[final] = value


def _walk(value: Any, path: tuple[str, ...] = (), budget: list[int] | None = None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if budget[0] > 100000 or len(path) > 64:
        raise ValueError("Fixture JSON exceeds node/depth limits")
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, (*path, key), budget)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, (*path, str(index)), budget)
    else:
        yield path, value


def _json(raw: bytes, limit: int = MAX_FILE_BYTES) -> Any:
    if len(raw) > limit:
        raise ValueError("Fixture input exceeds the file limit")
    try:
        data = parse_json(raw.decode("utf-8-sig"))
        for _, value in _walk(data):
            if type(value) is float and not math.isfinite(value):
                raise ValueError("Fixture JSON numbers must be finite")
    except RecursionError as exc:
        raise ValueError("Fixture JSON exceeds nesting limits") from exc
    return data


def _text(value: Any, limit: int, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be nonempty text of at most {limit} characters")
    value.encode("utf-8")


def format_signature(text: str, syntax: str) -> dict[str, Any]:
    """Describe formatting behavior without retaining any original prose."""
    if len(text) > 65536:
        raise ValueError("Fixture text fields cannot exceed 65536 characters")
    fields: Counter[tuple[str, str, str]] = Counter()
    escapes = [0, 0]
    if syntax == "format":

        def visit(value: str, depth: int = 0) -> None:
            if depth > 4:
                raise ValueError("Nested format specifications exceed depth 4")
            for literal, field, spec, conversion in string.Formatter().parse(value):
                escapes[0] += literal.count("{")
                escapes[1] += literal.count("}")
                if field is not None:
                    if NAME.fullmatch(field) is None or conversion not in (
                        None,
                        "s",
                        "r",
                        "a",
                    ):
                        raise ValueError(
                            "Use named format fields; positional/attribute/index fields are unsupported"
                        )
                    fields[(field, conversion or "", spec or "")] += 1
                    if sum(fields.values()) > 2048:
                        raise ValueError("Fixture placeholder limit exceeded")
                    visit(spec or "", depth + 1)

        visit(text)
    elif syntax != "none":
        raise ValueError("Fixture text syntax must be format or none")
    return {
        "fields": [[*key, count] for key, count in sorted(fields.items())],
        "escaped_braces": escapes,
        "newlines": re.findall(r"\r\n|\r|\n", text),
    }


def read_rules(raw: bytes) -> dict[str, Any]:
    rules = _json(raw)
    required = {
        "schema_version",
        "records",
        "id",
        "text",
        "preserve",
        "document_preserve",
        "checks",
        "references",
        "consumers",
        "literal_dependencies",
        "cases",
    }
    if (
        not isinstance(rules, dict)
        or set(rules) != required
        or type(rules["schema_version"]) is not int
        or rules["schema_version"] != 1
    ):
        raise ValueError("Fixture rules need exactly the documented version-1 fields")
    pointer(rules["records"])
    if not pointer(rules["id"]):
        raise ValueError("Record id must name a field")
    fields = rules["text"]
    if not isinstance(fields, dict) or not 1 <= len(fields) <= 64:
        raise ValueError("Declare 1-64 exact record text fields")
    for path, rule in fields.items():
        if not pointer(path) or path == rules["id"]:
            raise ValueError(
                "Text replacement cannot target a record root or its stable id"
            )
        if (
            not isinstance(rule, dict)
            or set(rule) != {"template", "syntax", "optional", "variants"}
            or type(rule["optional"]) is not bool
            or rule["syntax"] not in ("format", "none")
        ):
            raise ValueError("Text fields need template, syntax, optional and variants")
        if (
            not isinstance(rule["variants"], dict)
            or len(rule["variants"]) > MAX_RECORDS
        ):
            raise ValueError(
                "Text variants map at most 256 record IDs to explicit templates"
            )
        for value in (rule["template"], *rule["variants"].values()):
            if not isinstance(value, str):
                raise TypeError("Neutral templates must be strings")
            format_signature(value, rule["syntax"])
    for key in ("preserve", "document_preserve"):
        if (
            not isinstance(rules[key], list)
            or len(rules[key]) > 256
            or len(set(rules[key])) != len(rules[key])
        ):
            raise ValueError(
                "Preserve selectors must be a unique list of at most 256 pointers"
            )
        for value in rules[key]:
            pointer(value, wildcards=True)
    checks = rules["checks"]
    if not isinstance(checks, list) or len(checks) > 128:
        raise ValueError("Declare at most 128 invariant checks")
    ids = set()
    for check in checks:
        if (
            not isinstance(check, dict)
            or not isinstance(check.get("id"), str)
            or NAME.fullmatch(check["id"]) is None
            or check["id"] in ids
        ):
            raise ValueError("Invariant checks need unique identifier names")
        ids.add(check["id"])
        kind = check.get("kind")
        keys = {"id", "kind"}
        if kind == "type":
            keys |= {"path", "type", "required"}
            extras = {"minimum", "maximum", "min_length", "max_length", "enum"}
            if (
                not keys <= set(check)
                or set(check) - keys - extras
                or type(check["required"]) is not bool
                or check["type"]
                not in (
                    "string",
                    "integer",
                    "number",
                    "boolean",
                    "array",
                    "object",
                    "null",
                )
            ):
                raise ValueError("Invalid type invariant schema")
            for name in ("minimum", "maximum"):
                if name in check and (
                    check["type"] not in ("integer", "number")
                    or type(check[name]) not in (int, float)
                    or (type(check[name]) is float and not math.isfinite(check[name]))
                ):
                    raise ValueError(
                        "Numeric bounds require finite numeric values and a numeric type"
                    )
            for name in ("min_length", "max_length"):
                if name in check and (
                    check["type"] not in ("string", "array", "object")
                    or type(check[name]) is not int
                    or not 0 <= check[name] <= 100000
                ):
                    raise ValueError(
                        "Length bounds require a string/array/object and integer 0-100000"
                    )
            for low, high in (("minimum", "maximum"), ("min_length", "max_length")):
                if low in check and high in check and check[low] > check[high]:
                    raise ValueError("Invariant lower bound exceeds upper bound")
            if "enum" in check and (
                not isinstance(check["enum"], list)
                or not 1 <= len(check["enum"]) <= 256
            ):
                raise ValueError("Invariant enum needs 1-256 choices")
            pointer(check["path"])
            continue
        if kind in ("count", "variables"):
            keys |= {"array", "count" if kind == "count" else "text"}
            paths = [
                check.get("array"),
                check.get("count" if kind == "count" else "text"),
            ]
        elif kind == "requires":
            keys |= {"when", "equals", "then"}
            if (
                not isinstance(check.get("then"), list)
                or not 1 <= len(check["then"]) <= 64
            ):
                raise ValueError("Conditional checks require 1-64 dependent fields")
            paths = [check.get("when"), *check["then"]]
        else:
            raise ValueError(
                "Invariant kind must be type, count, variables or requires"
            )
        if set(check) != keys:
            raise ValueError("Unexpected invariant fields")
        for path in paths:
            pointer(path)
        if kind == "variables" and (
            check["text"] not in fields or fields[check["text"]]["syntax"] != "format"
        ):
            raise ValueError("Variables checks require a declared format text field")
    if not isinstance(rules["references"], list) or len(rules["references"]) > 64:
        raise ValueError("Declare at most 64 record reference fields")
    for ref in rules["references"]:
        if (
            not isinstance(ref, dict)
            or set(ref) != {"path", "many", "optional"}
            or type(ref["many"]) is not bool
            or type(ref["optional"]) is not bool
        ):
            raise ValueError("Reference rules need path, many and optional")
        pointer(ref["path"])
    if (
        not isinstance(rules["consumers"], list)
        or not 1 <= len(rules["consumers"]) <= 32
    ):
        raise ValueError("Declare 1-32 actual Python consumer call paths")
    for consumer in rules["consumers"]:
        if not isinstance(consumer, dict) or set(consumer) != {
            "path",
            "symbol",
            "fields",
            "purpose",
        }:
            raise ValueError("Consumers need path, symbol, fields and purpose")
        _text(consumer["path"], 1024, "Consumer path")
        _text(consumer["symbol"], 256, "Consumer symbol")
        if any(NAME.fullmatch(part) is None for part in consumer["symbol"].split(".")):
            raise ValueError("Consumer symbols must be Python qualified names")
        _text(consumer["purpose"], 1024, "Consumer purpose")
        if (
            not isinstance(consumer["fields"], list)
            or not 1 <= len(consumer["fields"]) <= 64
        ):
            raise ValueError("Consumers must identify 1-64 fields they process")
        for path in consumer["fields"]:
            pointer(path)
    if (
        not isinstance(rules["literal_dependencies"], list)
        or len(rules["literal_dependencies"]) > 64
    ):
        raise ValueError(
            "Explicitly declare literal_dependencies as a bounded list; [] means none"
        )
    cases = rules["cases"]
    if not isinstance(cases, list) or len(cases) > MAX_CASES:
        raise ValueError("Declare at most 32 explicit boundary/error cases")
    case_names = {"baseline"}
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "name",
            "record",
            "set",
            "remove",
            "expect",
        }:
            raise ValueError("Cases need name, record, set, remove and expect")
        if (
            not isinstance(case["name"], str)
            or NAME.fullmatch(case["name"]) is None
            or case["name"] in case_names
        ):
            raise ValueError(
                "Case names must be unique identifiers; baseline is reserved"
            )
        case_names.add(case["name"])
        _text(case["record"], 256, "Case record ID")
        if (
            not isinstance(case["set"], dict)
            or not isinstance(case["remove"], list)
            or len(case["set"]) + len(case["remove"]) > 64
        ):
            raise ValueError("Cases permit at most 64 explicit field mutations")
        paths = [*case["set"], *case["remove"]]
        if len(set(paths)) != len(paths):
            raise ValueError("Case set/remove paths must be unique and disjoint")
        parsed = [pointer(path) for path in paths]
        if any(not part for part in parsed) or any(
            a != b and a[: len(b)] == b for a in parsed for b in parsed
        ):
            raise ValueError(
                "Case mutation fields cannot overlap or replace the record root"
            )
        if (
            not isinstance(case["expect"], list)
            or len(case["expect"]) > 140
            or any(
                not isinstance(code, str)
                or code
                not in ids | {"@records", "@id", "@reference", "@text", "@format"}
                for code in case["expect"]
            )
        ):
            raise ValueError(
                "Expected case diagnostics must name declared checks or builtin invariant codes"
            )
    return rules


class Findings:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.count = 0
        self.codes: set[str] = set()

    def add(self, code: str, path: str, message: str) -> None:
        self.count += 1
        self.codes.add(code)
        if len(self.items) < MAX_DIAGNOSTICS:
            self.items.append({"code": code, "path": path, "message": message})

    def report(self) -> dict[str, Any]:
        return {
            "ok": not self.count,
            "diagnostics": self.items,
            "error_count": self.count,
            "diagnostics_complete": self.count == len(self.items),
            "codes": sorted(self.codes),
        }


def validate_data(
    data: Any, rules: dict[str, Any], *, signatures: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    findings = Findings()
    records = at(data, rules["records"])
    if (
        not isinstance(records, list)
        or not 1 <= len(records) <= MAX_RECORDS
        or any(not isinstance(record, dict) for record in records)
    ):
        findings.add("@records", rules["records"], "Expected 1-256 object records")
        return findings.report()
    ids = [at(record, rules["id"]) for record in records]
    valid_ids = {
        value for value in ids if isinstance(value, str) and 0 < len(value) <= 256
    }
    if len(valid_ids) != len(ids):
        findings.add(
            "@id",
            rules["id"],
            "Record IDs must be distinct nonempty strings of at most 256 characters",
        )
    if signatures is not None and ids != [entry["id"] for entry in signatures]:
        findings.add(
            "@id",
            rules["id"],
            "Record IDs/order differ from the prepared fixture contract",
        )
    for index, record in enumerate(records):
        prefix = rules["records"] + f"/{index}"
        for path, rule in rules["text"].items():
            value = at(record, path)
            if value is MISSING and rule["optional"]:
                continue
            if not isinstance(value, str):
                findings.add(
                    "@text", prefix + path, "Selected text must be present and a string"
                )
                continue
            try:
                signature = format_signature(value, rule["syntax"])
                if (
                    signatures is not None
                    and index < len(signatures)
                    and signature != signatures[index]["fields"].get(path)
                ):
                    findings.add(
                        "@format",
                        prefix + path,
                        "Placeholder multiplicity/specifiers, escaped braces or line endings changed",
                    )
            except ValueError as exc:
                findings.add("@format", prefix + path, str(exc))
        for check in rules["checks"]:
            kind = check["kind"]
            problem = False
            location = check.get("path", check.get("array", check.get("when", "")))
            if kind == "type":
                value = at(record, check["path"])
                if value is MISSING and not check["required"]:
                    continue
                types = {
                    "string": str,
                    "integer": int,
                    "boolean": bool,
                    "array": list,
                    "object": dict,
                    "null": type(None),
                }
                valid = (
                    (
                        type(value) is int
                        or (type(value) is float and math.isfinite(value))
                    )
                    if check["type"] == "number"
                    else type(value) is types.get(check["type"])
                )
                problem = not valid
                if valid:
                    if "minimum" in check:
                        problem |= value < check["minimum"]
                    if "maximum" in check:
                        problem |= value > check["maximum"]
                    if "min_length" in check and isinstance(value, (str, list, dict)):
                        problem |= len(value) < check["min_length"]
                    if "max_length" in check and isinstance(value, (str, list, dict)):
                        problem |= len(value) > check["max_length"]
                    if "enum" in check:
                        problem |= not any(
                            type(value) is type(choice) and value == choice
                            for choice in check["enum"]
                        )
            elif kind == "count":
                values, count = at(record, check["array"]), at(record, check["count"])
                problem = (
                    not isinstance(values, list)
                    or type(count) is not int
                    or len(values) != count
                )
            elif kind == "variables":
                values, text = at(record, check["array"]), at(record, check["text"])
                try:
                    fields = (
                        {
                            field[0]
                            for field in format_signature(text, "format")["fields"]
                        }
                        if isinstance(text, str)
                        else None
                    )
                    problem = (
                        not isinstance(values, list)
                        or any(not isinstance(value, str) for value in values)
                        or len(set(values)) != len(values)
                        or set(values) != fields
                    )
                except ValueError:
                    problem = True
            elif kind == "requires":
                value = at(record, check["when"])
                if type(value) is type(check["equals"]) and value == check["equals"]:
                    problem = any(
                        at(record, field) is MISSING for field in check["then"]
                    )
            if problem:
                findings.add(check["id"], prefix + location, f"{kind} invariant failed")
        for ref in rules["references"]:
            value = at(record, ref["path"])
            if value is MISSING and ref["optional"]:
                continue
            values = value if ref["many"] else [value]
            if not isinstance(values, list) or any(
                not isinstance(item, str) or item not in valid_ids for item in values
            ):
                findings.add(
                    "@reference",
                    prefix + ref["path"],
                    "Reference must resolve to a declared record ID",
                )
    return findings.report()


def _selected(
    path: tuple[str, ...], patterns: list[tuple[str, ...]], document: Any
) -> bool:
    for pattern in patterns:
        if len(path) != len(pattern):
            continue
        node = document
        matched = True
        for actual, expected in zip(path, pattern, strict=True):
            if expected != actual and not (expected == "*" and isinstance(node, list)):
                matched = False
                break
            node = node[int(actual)] if isinstance(node, list) else node[actual]
        if matched:
            return True
    return False
