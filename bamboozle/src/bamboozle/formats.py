"""Explicit document adapters over the semantic cipher, preserving technical syntax."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from bamboozle.dictionary import Dictionary
from bamboozle.engine import Mode, Translator
from bamboozle.markers import CURRENT
from bamboozle.python_source import PythonSource, selections
from bamboozle.utils import MAX_FILE_BYTES, parse_json

MAX_POINTERS = 512
MAX_JSON_NODES = 100000
MAX_DEPTH = 64


@dataclass(frozen=True)
class FormatRules:
    format: str = "text"
    pointers: tuple[str, ...] = ()
    identifiers: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    strings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.format not in ("text", "markdown", "json", "python"):
            raise ValueError("Document format must be text, markdown, json, or python")
        if self.format == "python":
            selections(self.identifiers, self.modules, self.strings)
        elif self.identifiers or self.modules or self.strings:
            raise ValueError("Python selections apply only to python format")
        if not isinstance(self.pointers, tuple) or any(
            not isinstance(p, str) for p in self.pointers
        ):
            raise ValueError("JSON pointers must be a tuple of strings")
        if self.format != "json" and self.pointers:
            raise ValueError("JSON pointers apply only to json format")
        if self.format == "json" and not 1 <= len(self.pointers) <= MAX_POINTERS:
            raise ValueError(
                "JSON translation requires 1 to 512 exact string-value pointers"
            )
        if len(set(self.pointers)) != len(self.pointers):
            raise ValueError("Duplicate JSON pointer")
        for pointer in self.pointers:
            if (
                len(pointer) > 2048
                or (pointer and not pointer.startswith("/"))
                or re.search(r"~(?![01])", pointer)
            ):
                raise ValueError(
                    "Invalid JSON pointer; use / segments and ~0/~1 escapes"
                )

    def document(self) -> dict[str, Any]:
        return {
            "format": self.format,
            **({"pointers": list(self.pointers)} if self.format == "json" else {}),
            **(
                {
                    "identifiers": list(self.identifiers),
                    "modules": list(self.modules),
                    "strings": list(self.strings),
                }
                if self.format == "python"
                else {}
            ),
        }

    @classmethod
    def from_document(cls, value: Any) -> FormatRules:
        if value is None:
            return cls()
        if not isinstance(value, dict) or not isinstance(value.get("format"), str):
            raise TypeError("Format rules require a format object")
        if value["format"] == "python":
            if (
                not {"format", "identifiers"} <= value.keys()
                or set(value) - {"format", "identifiers", "modules", "strings"}
                or any(
                    not isinstance(value.get(key, []), list)
                    for key in ("identifiers", "modules", "strings")
                )
            ):
                raise ValueError(
                    "Python rules require identifiers and optional modules/strings lists"
                )
            return cls(
                "python",
                identifiers=tuple(value["identifiers"]),
                modules=tuple(value.get("modules", [])),
                strings=tuple(value.get("strings", [])),
            )
        expected = {"format", "pointers"} if value["format"] == "json" else {"format"}
        if set(value) != expected or (
            "pointers" in value and not isinstance(value["pointers"], list)
        ):
            raise ValueError("Unexpected format rules or missing JSON pointers")
        return cls(value["format"], tuple(value.get("pointers", [])))


def format_options(
    format: str | None,
    pointers: list[str] | None,
    identifiers: list[str] | None = None,
    modules: list[str] | None = None,
    strings: list[str] | None = None,
) -> FormatRules | None:
    if format is None:
        if identifiers or modules or strings:
            raise ValueError(
                "--python-identifier/--python-module/--python-string require --format python"
            )
        if pointers:
            raise ValueError("--json-pointer requires --format json")
        return None
    return FormatRules(
        format,
        tuple(pointers or []),
        tuple(identifiers or []),
        tuple(modules or []),
        tuple(strings or []),
    )


@dataclass(frozen=True)
class DocumentTranslation:
    text: str
    substitutions: int
    literals: int
    context: dict[str, Any] = field(default_factory=dict)
    escapes: int = 0


@dataclass(frozen=True)
class TextRegion:
    """One translated region; JSON locations refer to its decoded string value."""

    start: int
    end: int
    value: str
    pointer: str | None = None


def _json_strings(text: str) -> dict[str, tuple[int, int, str]]:
    """Validate JSON and locate value tokens without reserializing the document."""
    offset = 1 if text.startswith("\ufeff") else 0
    try:
        parse_json(text[offset:])
    except RecursionError as exc:
        raise ValueError("JSON nesting limit exceeded") from exc
    decoder = json.JSONDecoder()
    strings: dict[str, tuple[int, int, str]] = {}
    nodes = 0

    def whitespace(index: int) -> int:
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        return index

    def value(index: int, pointer: str, depth: int) -> int:
        nonlocal nodes
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_JSON_NODES:
            raise ValueError("JSON nesting or node limit exceeded")
        index = whitespace(index)
        if text[index] == "{":
            index = whitespace(index + 1)
            while text[index] != "}":
                key, end = decoder.raw_decode(text, index)
                part = key.replace("~", "~0").replace("/", "~1")
                index = value(whitespace(end) + 1, pointer + "/" + part, depth + 1)
                index = whitespace(index)
                if text[index] == ",":
                    index = whitespace(index + 1)
            return index + 1
        if text[index] == "[":
            index = whitespace(index + 1)
            item = 0
            while text[index] != "]":
                index = whitespace(value(index, pointer + f"/{item}", depth + 1))
                item += 1
                if text[index] == ",":
                    index = whitespace(index + 1)
            return index + 1
        decoded, end = decoder.raw_decode(text, index)
        if isinstance(decoded, str):
            strings[pointer] = (index, end, decoded)
        return end

    value(offset, "", 0)
    return strings


def _merged(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def _markdown_protected(text: str) -> list[tuple[int, int]]:
    from markdown_it import MarkdownIt, rules_inline

    if len(text) > 1000000:
        raise ValueError("Markdown adapter exceeds its 1000000-character parser limit")
    parser = MarkdownIt("commonmark")
    bom = 1 if text.startswith("\ufeff") else 0
    body = text[bom:]
    # Cipher markers are vocabulary tokens, not Markdown emphasis. Mask them for
    # syntax discovery only; the actual translator still validates/decodes them.
    syntax = re.sub(
        r"__(?:SCM_LIT|SCM_CASE)_[^\r\n]*?(?:__|(?=\r?$))",
        lambda match: "x" * len(match.group()),
        body,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    lines = [0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", body))]
    lines.append(len(body))
    environment: dict[str, Any] = {}
    spans: list[tuple[int, int]] = []
    tokens = parser.parse(syntax, environment)
    for token in tokens:
        if token.type in {"fence", "code_block", "html_block"} and token.map:
            spans.append((lines[token.map[0]], lines[token.map[1]]))
    for reference in [
        *environment.get("references", {}).values(),
        *environment.get("duplicate_refs", []),
    ]:
        start, end = reference["map"]
        spans.append((lines[start], lines[end]))
    # Parse inline syntax over original characters to retain exact byte positions.
    # Block masks preserve line breaks so code cannot open inline markup in prose.
    masked = list(syntax)
    for start, end in spans:
        masked[start:end] = [
            char if char in "\r\n" else " " for char in syntax[start:end]
        ]
    inline_text = "".join(masked)
    inline_start = 0
    inline_source = ""
    emphasis: list[tuple[int, Any]] = []

    def track_emphasis(state, silent):
        start = state.pos
        matched = rules_inline.emphasis.tokenize(state, silent)
        if matched and not silent and state.src is inline_source:
            length = state.pos - start
            emphasis.extend(
                (inline_start + start + index, token)
                for index, token in enumerate(state.tokens[-length:])
            )
        return matched

    parser.inline.ruler.at("emphasis", track_emphasis)

    def protect(rule):
        def wrapped(state, silent):
            start = state.pos
            matched = rule(state, silent)
            if matched and not silent and state.src is inline_source:
                spans.append((inline_start + start, inline_start + state.pos))
            return matched

        return wrapped

    for name, rule in (
        ("backticks", rules_inline.backtick),
        ("link", rules_inline.link),
        ("image", rules_inline.image),
        ("autolink", rules_inline.autolink),
        ("html_inline", rules_inline.html_inline),
        ("escape", rules_inline.escape),
        ("entity", rules_inline.entity),
    ):
        parser.inline.ruler.at(name, protect(rule))
    # Inline constructs cannot span distinct CommonMark blocks. Parse each block
    # with its original line prefixes intact, then lift offsets into the document.
    for token in tokens:
        if token.type == "inline" and token.map:
            inline_start, inline_end = lines[token.map[0]], lines[token.map[1]]
            inline_source = inline_text[inline_start:inline_end]
            parser.inline.parse(inline_source, parser, environment, [])
    for start, token in emphasis:
        if token.type == "strong_open":
            spans.append((start - 1, start + 1))
        elif token.type == "strong_close":
            spans.append((start, start + 2))
        elif token.type in {"em_open", "em_close"}:
            spans.append((start, start + 1))
    # Bare addresses and portable/Windows paths are technical literals even when
    # they are not written as CommonMark links or code spans.
    technical = re.compile(
        r"(?i)(?<![\w.+-])(?:[a-z][a-z0-9+.-]*://|mailto:|www\.)[^\s<>]+"
        r"|(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}"
        r"|(?<![\w.-])(?:[a-z]:[/\\]|[.~]{0,2}/|~\\|[\w.-]+[/\\])[^\s<>\[\]()`]+"
        r"|(?<![\w.-])[\w.-]+\.[a-z0-9]{1,12}(?![\w.])"
    )
    spans.extend(match.span() for match in technical.finditer(syntax))
    return [(start + bom, end + bom) for start, end in _merged(spans)]


class DocumentCodec:
    """Shared adapter; original JSON spellings are retained only in private context."""

    def __init__(
        self,
        dictionary: Dictionary,
        rules: FormatRules | None = None,
        *,
        limit: int = MAX_FILE_BYTES,
        marker_version: int = CURRENT,
    ):
        self.rules = rules or FormatRules()
        self.translator = Translator(
            dictionary, max_output_chars=limit, marker_version=marker_version
        )
        self.limit = limit
        self.python = (
            PythonSource(
                self.translator,
                self.rules.identifiers,
                self.rules.modules,
                self.rules.strings,
            )
            if self.rules.format == "python"
            else None
        )

    def validate_context(self, context: Any) -> dict[str, Any]:
        if self.python is not None:
            return self.python.context(context)
        if not isinstance(context, dict) or (self.rules.format != "json" and context):
            raise ValueError("Invalid document restoration context")
        if not set(context).issubset(self.rules.pointers):
            raise ValueError("JSON restoration context contains an unselected pointer")
        total = 0
        for pointer, pair in context.items():
            if not isinstance(pair, dict) or set(pair) != {"original", "encoded"}:
                raise ValueError(f"Invalid JSON token context: {pointer}")
            if any(not isinstance(token, str) for token in pair.values()):
                raise ValueError("JSON context tokens must be strings")
            total += sum(len(token) for token in pair.values())
            if total > self.limit * 2:
                raise ValueError("Document context exceeds its size limit")
            original = parse_json(pair["original"])
            encoded = parse_json(pair["encoded"])
            if not isinstance(original, str) or not isinstance(encoded, str):
                raise TypeError("JSON context must contain string values")
            transformed = self.translator.forward(original)
            if (
                transformed != encoded
                or self._token(original, pair["original"], transformed)
                != pair["encoded"]
            ):
                raise ValueError("JSON context does not match the saved dictionary")
        return context

    def regions(self, text: str) -> list[TextRegion]:
        """Expose the exact translation boundary to operator return validation."""
        if len(text.encode("utf-8")) > self.limit:
            raise ValueError("Document exceeds size limit")
        if self.rules.format == "text":
            return [TextRegion(0, len(text), text)]
        if self.python is not None:
            _, spans = self.python.spans(text)
            return [
                TextRegion(
                    span.start,
                    span.end,
                    span.value,
                    f"python-string:{span.start}" if span.kind == "STRING" else None,
                )
                for span in spans
            ]
        if self.rules.format == "json":
            strings = _json_strings(text)
            regions = []
            for pointer in self.rules.pointers:
                if pointer not in strings:
                    raise ValueError(
                        f"JSON pointer is missing or does not select a string: {pointer!r}"
                    )
                start, end, value = strings[pointer]
                regions.append(TextRegion(start, end, value, pointer))
            return sorted(regions, key=lambda region: region.start)
        regions = []
        cursor = 0
        for start, end in [*_markdown_protected(text), (len(text), len(text))]:
            if start > cursor:
                regions.append(TextRegion(cursor, start, text[cursor:start]))
            cursor = end
        return regions

    @staticmethod
    def _token(original: str, raw: str, result: str) -> str:
        return raw if result == original else json.dumps(result, ensure_ascii=False)

    def transform(
        self,
        text: str,
        mode: Mode = "forward",
        *,
        context: dict[str, Any] | None = None,
    ) -> DocumentTranslation:
        if mode not in ("forward", "reverse") or len(text.encode("utf-8")) > self.limit:
            raise ValueError("Invalid document mode or document exceeds size limit")
        saved = self.validate_context(context if context is not None else {})
        if mode == "forward" and saved:
            raise ValueError("Saved document context is only used for decoding")
        result = self._transform(text, mode, saved)
        if len(result.text.encode("utf-8")) > self.limit:
            raise ValueError("Translated document exceeds its byte limit")
        if (
            mode == "forward"
            and self._transform(result.text, "reverse", result.context).text != text
        ):
            raise ValueError("Document syntax prevents exact reversible translation")
        return result

    def _transform(
        self, text: str, mode: Mode, context: dict[str, Any]
    ) -> DocumentTranslation:
        if self.python is not None:
            python_text, substitutions, saved = self.python.transform(
                text, mode, context
            )
            return DocumentTranslation(python_text, substitutions, 0, saved)
        if self.rules.format == "text":
            result = self.translator.transform(text, mode)
            return DocumentTranslation(
                result.text,
                result.substitutions,
                result.literals,
                escapes=result.escapes,
            )
        replacements: list[tuple[int, int, str]] = []
        restored: dict[str, Any] = {}
        substitutions = literals = escapes = 0
        output_bytes = len(text.encode("utf-8"))

        def replace(start: int, end: int, rendered: str) -> None:
            nonlocal output_bytes
            output_bytes += len(rendered.encode("utf-8")) - len(
                text[start:end].encode("utf-8")
            )
            if output_bytes > self.limit:
                raise ValueError("Translated document exceeds its byte limit")
            replacements.append((start, end, rendered))

        for region in self.regions(text):
            start, end, value, pointer = (
                region.start,
                region.end,
                region.value,
                region.pointer,
            )
            if self.rules.format == "json":
                assert pointer is not None
                raw = text[start:end]
                translated = self.translator.transform(value, mode)
                rendered = self._token(value, raw, translated.text)
                if (
                    mode == "reverse"
                    and pointer in context
                    and raw == context[pointer]["encoded"]
                ):
                    rendered = context[pointer]["original"]
                if mode == "forward" and rendered != raw:
                    restored[pointer] = {"original": raw, "encoded": rendered}
                replace(start, end, rendered)
                substitutions += translated.substitutions
                literals += translated.literals
                escapes += translated.escapes
            else:
                translated = self.translator.transform(value, mode)
                replace(start, end, translated.text)
                substitutions += translated.substitutions
                literals += translated.literals
                escapes += translated.escapes
        pieces: list[str] = []
        cursor = 0
        for start, end, rendered in sorted(replacements):
            pieces.extend((text[cursor:start], rendered))
            cursor = end
        pieces.append(text[cursor:])
        return DocumentTranslation(
            "".join(pieces), substitutions, literals, restored, escapes
        )
