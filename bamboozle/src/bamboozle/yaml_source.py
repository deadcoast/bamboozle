"""Selected YAML scalar edits over parser spans, never a whole-document emitter.

The supported language is a single YAML 1.2 core-schema document with unique
string mapping keys. PyYAML supplies syntax events/tokens, not constructors or
its YAML 1.1 implicit resolver. See https://yaml.org/spec/1.2.2/#103-core-schema
and https://pyyaml.org/wiki/PyYAMLDocumentation (scan/parse APIs).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from yaml import events as ev
from yaml.tokens import ScalarToken

from bamboozle.engine import Mode, Translator

MAX_CHARS = 1000000
MAX_NODES = 100000
MAX_DEPTH = 64
STR = "tag:yaml.org,2002:str"
BREAK = re.compile(r"\r\n|[\r\n\x85\u2028\u2029]")
CORE = re.compile(
    r"(?:null|Null|NULL|~|true|True|TRUE|false|False|FALSE|"
    r"[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+|"
    r"[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?|"
    r"[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)|)"
)


@dataclass(eq=False)
class Node:
    kind: str
    tag: str | None
    value: str = ""
    token: ScalarToken | None = None
    children: list[tuple[str, Node]] = field(default_factory=list)
    key: bool = False
    owner: str = ""
    paths: set[str] = field(default_factory=set)
    merged: bool = False


def _string(event: ev.ScalarEvent) -> bool:
    if event.tag is not None:
        return event.tag in (STR, "!")
    return event.style is not None or CORE.fullmatch(event.value) is None


def _part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


class YamlDocument:
    """Bounded serialization graph with physical scalar ownership and alias paths."""

    def __init__(self, text: str):
        if len(text) > MAX_CHARS:
            raise ValueError("YAML exceeds its 1000000-character parser limit")
        self.text = text
        self.nodes: list[Node] = []
        self.paths: dict[str, Node] = {}
        try:
            tokens = {}
            for count, token in enumerate(yaml.scan(text, Loader=yaml.SafeLoader), 1):
                if count > MAX_NODES * 4:
                    raise ValueError("YAML token limit exceeded")
                if isinstance(token, ScalarToken):
                    tokens[token.end_mark.index] = token
            events = iter(yaml.parse(text, Loader=yaml.SafeLoader))
            anchors: dict[str, Node] = {}
            assert isinstance(next(events), ev.StreamStartEvent)
            start = next(events)
            if not isinstance(start, ev.DocumentStartEvent):
                raise TypeError("YAML requires exactly one document")
            if start.version not in (None, (1, 2)):
                raise ValueError("YAML adapter requires version 1.2")

            def node(event: Any, depth: int, path: str) -> Node:
                if depth > MAX_DEPTH or len(self.nodes) >= MAX_NODES:
                    raise ValueError("YAML nesting or node limit exceeded")
                if isinstance(event, ev.AliasEvent):
                    if event.anchor not in anchors:
                        raise ValueError("YAML alias has no preceding anchor")
                    return anchors[event.anchor]
                if isinstance(event, ev.ScalarEvent):
                    item = Node("string" if _string(event) else "scalar", event.tag)
                    item.value = event.value
                    assert event.end_mark is not None
                    item.token = tokens.get(event.end_mark.index)
                    if item.token is None and item.kind == "string" and not event.value:
                        item.token = ScalarToken(
                            "", True, event.end_mark, event.end_mark
                        )
                elif isinstance(event, (ev.MappingStartEvent, ev.SequenceStartEvent)):
                    item = Node(
                        "map" if isinstance(event, ev.MappingStartEvent) else "seq",
                        event.tag,
                    )
                else:
                    raise TypeError("Unexpected YAML node")
                item.owner = path
                self.nodes.append(item)
                if event.anchor:
                    if event.anchor in anchors:
                        raise ValueError("Duplicate YAML anchor is ambiguous")
                    anchors[event.anchor] = item
                if item.kind == "map":
                    keys: set[str] = set()
                    while not isinstance(key_event := next(events), ev.MappingEndEvent):
                        key = node(key_event, depth + 1, path)
                        if key.kind != "string" or key.token is None:
                            raise ValueError(
                                "YAML mappings require explicit string keys"
                            )
                        key.key = True
                        if key.value in keys:
                            raise ValueError("Duplicate YAML mapping key")
                        keys.add(key.value)
                        child = node(
                            next(events), depth + 1, path + "/" + _part(key.value)
                        )
                        item.children.append((key.value, child))
                elif item.kind == "seq":
                    while not isinstance(
                        child_event := next(events), ev.SequenceEndEvent
                    ):
                        index = str(len(item.children))
                        item.children.append(
                            (index, node(child_event, depth + 1, path + "/" + index))
                        )
                return item

            root = node(next(events), 0, "")
            if not isinstance(next(events), ev.DocumentEndEvent) or not isinstance(
                next(events), ev.StreamEndEvent
            ):
                raise TypeError(
                    "YAML requires exactly one document; split document streams explicitly"
                )

            def visit(
                item: Node, path: str, ancestors: frozenset[Node], merged: bool = False
            ) -> None:
                if item in ancestors:
                    raise ValueError("Cyclic YAML aliases are not supported")
                if len(ancestors) > MAX_DEPTH or len(self.paths) >= MAX_NODES:
                    raise ValueError("YAML alias expansion or nesting limit exceeded")
                self.paths[path] = item
                item.paths.add(path)
                item.merged |= merged
                for key, child in item.children:
                    visit(
                        child,
                        path + "/" + _part(key),
                        ancestors | {item},
                        merged or (item.kind == "map" and key == "<<"),
                    )

            visit(root, "", frozenset())
        except (yaml.YAMLError, RecursionError, StopIteration, TypeError) as exc:
            raise ValueError(f"Invalid YAML syntax: {exc}") from exc

    def selected(self, pointers: tuple[str, ...]) -> list[Node]:
        selected = set(pointers)
        physical: set[Node] = set()
        for pointer in pointers:
            item = self.paths.get(pointer)
            if item is None or item.kind != "string" or item.token is None:
                raise ValueError(
                    f"YAML pointer is missing or does not select a string: {pointer!r}"
                )
            if item.key or item.merged:
                raise ValueError(
                    "YAML keys and merge-owned values cannot be translated"
                )
            if not item.paths <= selected:
                missing = sorted(item.paths - selected)
                raise ValueError(
                    f"Select every YAML alias value path explicitly; missing {missing[:8]!r}"
                )
            physical.add(item)
        return sorted(
            physical, key=lambda item: item.token.start_mark.index if item.token else -1
        )

    def signature(self) -> list[tuple[Any, ...]]:
        """Topology, tags and values including keys, without constructing objects."""
        return [
            (
                item.kind,
                item.tag,
                item.value,
                item.key,
                tuple(sorted(item.paths)),
                tuple(key for key, _ in item.children),
            )
            for item in self.nodes
        ]


def _indent(text: str, start: int) -> int:
    line = BREAK.split(text[:start])[-1].lstrip("\ufeff")
    return len(line) - len(line.lstrip(" "))


def _value(raw: str, indent: int) -> str:
    """Read one isolated scalar spelling; no tag constructors are called."""
    document = YamlDocument(" " * indent + "v: " + raw)
    if len(document.paths) != 2 or "/v" not in document.paths:
        raise ValueError("YAML context is not one scalar token")
    item = document.paths["/v"]
    if item.kind not in ("string", "scalar") or item.token is None:
        raise ValueError("YAML context must retain a scalar spelling")
    token = item.token
    # Block scanners include the terminal line ending. Additional token material
    # must not smuggle siblings/comments into a private restoration context.
    start = indent + 3
    if token.start_mark.index != start or document.text[
        start : token.end_mark.index
    ].rstrip("\r\n") != raw.rstrip("\r\n"):
        raise ValueError("YAML context contains material outside its scalar")
    return item.value


def _render(
    raw: str, value: str, result: str, indent: int, translator: Translator, mode: Mode
) -> str:
    if value == result:
        return raw
    header = ""
    body = raw
    if raw.startswith(("|", ">")):
        match = BREAK.search(raw)
        if match is not None:
            header, body = raw[: match.end()], raw[match.end() :]
    try:
        candidate = header + translator.transform(body, mode).text
        if _value(candidate, indent) == result and (
            raw.startswith(("'", '"', "|", ">")) or CORE.fullmatch(result) is None
        ):
            return candidate
    except ValueError:
        pass
    if raw.startswith("'") and not BREAK.search(result):
        return "'" + result.replace("'", "''") + "'"
    if raw.startswith(("|", ">")):
        ending = BREAK.search(raw)
        newline = ending.group() if ending else "\n"
        rendered = yaml.safe_dump(
            result,
            default_style=raw[0],
            allow_unicode=True,
            width=1000000,
            line_break=newline,
        )
        scalar = next(
            token for token in yaml.scan(rendered) if isinstance(token, ScalarToken)
        )
        rendered = rendered[scalar.start_mark.index : scalar.end_mark.index]
        lines = rendered.splitlines(keepends=True)
        if rendered.startswith(("|", ">")):
            content_indents = [
                len(line) - len(line.lstrip(" "))
                for line in body.splitlines()
                if line.strip()
            ]
            block_indent = min(content_indents, default=indent + 2)
            preserved = header + "".join(
                " " * block_indent + line[2:] if line.startswith("  ") else line
                for line in lines[1:]
            )
            if header and _value(preserved, indent) == result:
                return preserved
            comment = header.partition("#")[2]
            if comment:
                lines[0] = lines[0].rstrip("\r\n") + " #" + comment
            rendered = lines[0] + "".join(" " * indent + line for line in lines[1:])
            if _value(rendered, indent) == result:
                return rendered
    # JSON quoting is a YAML double-quoted spelling. Escape YAML-specific line
    # separators too: they are data here, not physical line folding characters.
    return (
        json.dumps(result, ensure_ascii=False)
        .replace("\x85", "\\N")
        .replace("\u2028", "\\L")
        .replace("\u2029", "\\P")
    )


class YamlSource:
    def __init__(self, translator: Translator, pointers: tuple[str, ...], limit: int):
        self.translator, self.pointers, self.limit = translator, pointers, limit

    def context(self, context: Any) -> dict[str, Any]:
        if not isinstance(context, dict) or not set(context) <= set(self.pointers):
            raise ValueError("Invalid YAML restoration context or unselected pointer")
        total = 0
        for pair in context.values():
            if not isinstance(pair, dict) or set(pair) != {
                "original",
                "encoded",
                "indent",
            }:
                raise ValueError("Invalid YAML scalar context")
            if (
                any(not isinstance(pair[key], str) for key in ("original", "encoded"))
                or type(pair["indent"]) is not int
                or not 0 <= pair["indent"] <= MAX_CHARS
            ):
                raise ValueError("Invalid YAML context spellings or indentation")
            total += len(pair["original"]) + len(pair["encoded"]) + pair["indent"]
            if total > self.limit * 2:
                raise ValueError("YAML context exceeds size limit")
            original = _value(pair["original"], pair["indent"])
            expected = self.translator.forward(original)
            if (
                _value(pair["encoded"], pair["indent"]) != expected
                or _render(
                    pair["original"],
                    original,
                    expected,
                    pair["indent"],
                    self.translator,
                    "forward",
                )
                != pair["encoded"]
            ):
                raise ValueError("YAML context does not match the saved dictionary")
        return context

    def transform(
        self, text: str, mode: Mode, context: dict[str, Any]
    ) -> tuple[str, int, int, int, dict[str, Any]]:
        document = YamlDocument(text)
        selected = document.selected(self.pointers)
        pieces: list[str] = []
        cursor = substitutions = literals = escapes = 0
        saved: dict[str, Any] = {}
        output_bytes = len(text.encode("utf-8"))
        for item in selected:
            assert item.token is not None
            start, end = item.token.start_mark.index, item.token.end_mark.index
            raw, indent = text[start:end], _indent(text, start)
            result = self.translator.transform(item.value, mode)
            rendered = _render(
                raw, item.value, result.text, indent, self.translator, mode
            )
            pair = context.get(item.owner)
            if (
                mode == "reverse"
                and pair
                and raw == pair["encoded"]
                and indent == pair["indent"]
            ):
                rendered = pair["original"]
            if mode == "forward" and raw != rendered:
                saved[item.owner] = {
                    "original": raw,
                    "encoded": rendered,
                    "indent": indent,
                }
            output_bytes += len(rendered.encode("utf-8")) - len(raw.encode("utf-8"))
            if output_bytes > self.limit:
                raise ValueError("Translated YAML exceeds its byte limit")
            pieces.extend((text[cursor:start], rendered))
            cursor = end
            substitutions += result.substitutions
            literals += result.literals
            escapes += result.escapes
            item.value = result.text
        pieces.append(text[cursor:])
        output = "".join(pieces)
        if len(output.encode("utf-8")) > self.limit:
            raise ValueError("Translated YAML exceeds its byte limit")
        # Independently parse the published candidate: detect new keys, changed
        # types, folding errors, anchor topology changes and leaked replacements.
        actual = YamlDocument(output)
        if actual.signature() != document.signature():
            raise ValueError(
                "YAML scalar replacement changed protected structure or type"
            )
        actual.selected(self.pointers)
        return output, substitutions, literals, escapes, saved


def protected(text: str, pointers: tuple[str, ...]) -> str:
    document = YamlDocument(text)
    pieces: list[str] = []
    cursor = 0
    for item in document.selected(pointers):
        assert item.token is not None
        start, end = item.token.start_mark.index, item.token.end_mark.index
        raw = text[start:end]
        header = ""
        if raw.startswith(("|", ">")) and (match := BREAK.search(raw)):
            header = raw[: match.end()]
        pieces.extend((text[cursor:start], header, f"<selected:{item.owner}>"))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)
