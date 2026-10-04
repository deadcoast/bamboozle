"""Version-1 technical literal boundaries for explicitly protected plain text.

The scanner recognizes spelling, never filesystem existence or URL reachability.
Selected literals stay visible and byte-identical; this is not secret redaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from bamboozle.engine import Mode, Translator
from bamboozle.utils import sha256

MAX_CHARS = 1_000_000
MAX_SPANS = 10_000
KINDS = ("paths", "urls")
START = re.compile(
    r"(?P<marker>__(?:SCM_LIT|SCM_CASE)_)"
    r"|(?P<quote>[\"'`])"
    r"|(?<![\w./\\-])(?P<url>[a-z][a-z0-9+.-]*://|mailto:|www\.)"
    r"|(?<![\w./\\-])(?P<path>[a-z]:[/\\]|\\\\|(?:\.{0,2}|~)[/\\]|[\w.-]+[/\\]|[\w-]+(?:\.[\w-]+)*\.[a-z][a-z0-9_-]{0,31}(?![\w.]))",
    re.IGNORECASE,
)
STOP = re.compile(r"[\s<>\"'`|]")


@dataclass(frozen=True)
class LiteralSpan:
    start: int
    end: int
    kind: str
    value: str


def validate_selection(kinds: tuple[str, ...]) -> None:
    if (
        not isinstance(kinds, tuple)
        or len(kinds) > len(KINDS)
        or any(kind not in KINDS for kind in kinds)
        or len(set(kinds)) != len(kinds)
    ):
        raise ValueError("Text protection selects paths and/or urls once each")


def spans(
    text: str, kinds: tuple[str, ...], translator: Translator, mode: Mode
) -> list[LiteralSpan]:
    if len(text) > MAX_CHARS:
        raise ValueError("Protected text exceeds the 1000000-character analysis limit")
    result = []
    position = 0
    while (match := START.search(text, position)) is not None:
        start = match.start()
        if match.lastgroup == "quote":
            closing = text.find(match.group(), match.end())
            body = text[match.end() : closing] if closing >= 0 else ""
            kind = ""
            if body and not re.search(r"[\r\n<>|]", body):
                if re.match(
                    r"(?:[a-z][a-z0-9+.-]*://|mailto:|www\.)", body, re.IGNORECASE
                ):
                    if not re.search(r"\s", body):
                        kind = "urls"
                elif re.match(r"(?:[\w.-]+(?:[ \t]+[\w.-]+)*[/\\])", body) or (
                    (candidate := START.match(body)) is not None
                    and candidate.lastgroup == "path"
                ):
                    kind = "paths"
            if kind:
                position = closing + 1
                if kind in kinds:
                    result.append(
                        LiteralSpan(start, position, kind, text[start:position])
                    )
                    if len(result) > MAX_SPANS:
                        raise ValueError(
                            "Protected text exceeds the 10000-literal limit"
                        )
            else:
                position = match.end()
            continue
        if match.lastgroup == "marker":
            if mode == "reverse":
                position = translator.marker_at(text, start).end
            else:
                # Original marker examples are quoted by the normal translator;
                # no embedded URL/path may split that vocabulary token.
                ending = STOP.search(text, match.end())
                position = ending.start() if ending else len(text)
            continue
        kind = "urls" if match.lastgroup == "url" else "paths"
        stop = STOP.search(text, match.end())
        end = stop.start() if stop else len(text)
        quote = text[start - 1] if start else ""
        if quote and quote in "\"'`":
            closing = text.find(quote, match.end())
            newline = re.search(
                r"[\r\n]", text[match.end() : closing if closing >= 0 else end]
            )
            if closing >= 0 and newline is None and (kind == "paths" or closing == end):
                start -= 1
                end = closing + 1
        if start == match.start():
            # Prose punctuation is outside unquoted literals. Balanced URL/path
            # parentheses remain inside; unmatched enclosing closers stay prose.
            counts = {char: text.count(char, start, end) for char in "()[]{}"}
            while end > match.end() and text[end - 1] in ".,;:!?)]}":
                char = text[end - 1]
                if char in ")]}":
                    opening = {")": "(", "]": "[", "}": "{"}[char]
                    if counts[char] <= counts[opening]:
                        break
                    counts[char] -= 1
                end -= 1
        position = max(end, match.end())
        if kind in kinds:
            result.append(LiteralSpan(start, end, kind, text[start:end]))
            if len(result) > MAX_SPANS:
                raise ValueError("Protected text exceeds the 10000-literal limit")
    return result


class ProtectedText:
    def __init__(self, translator: Translator, kinds: tuple[str, ...], limit: int):
        validate_selection(kinds)
        self.translator, self.kinds, self.limit = translator, kinds, limit

    def spans(self, text: str, mode: Mode = "reverse") -> list[LiteralSpan]:
        return spans(text, self.kinds, self.translator, mode)

    def context(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError("Protected text context must be an object")
        if not value:
            return value
        if (
            set(value)
            != {
                "kind",
                "schema_version",
                "source_chars",
                "encoded_chars",
                "source_sha256",
                "encoded_sha256",
                "spans",
            }
            or value["kind"] != "bamboozle.text-literals"
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or not isinstance(value["spans"], list)
            or len(value["spans"]) > MAX_SPANS
        ):
            raise ValueError("Invalid protected text context")
        for key in ("source_chars", "encoded_chars"):
            if type(value[key]) is not int or not 0 <= value[key] <= min(
                MAX_CHARS, self.limit
            ):
                raise ValueError("Invalid protected text context length")
        for key in ("source_sha256", "encoded_sha256"):
            if (
                not isinstance(value[key], str)
                or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None
            ):
                raise ValueError("Invalid protected text context digest")
        previous = {"source": 0, "encoded": 0}
        size = 0
        for row in value["spans"]:
            if (
                not isinstance(row, dict)
                or set(row) != {"kind", "source", "encoded", "value"}
                or row["kind"] not in self.kinds
                or not isinstance(row["value"], str)
                or not row["value"]
            ):
                raise ValueError("Invalid protected literal context entry")
            size += len(row["value"].encode("utf-8"))
            if size > self.limit:
                raise ValueError("Protected literal context exceeds its byte limit")
            found = self.spans(row["value"], "forward")
            if len(found) != 1 or found[0] != LiteralSpan(
                0, len(row["value"]), row["kind"], row["value"]
            ):
                raise ValueError("Retained literal does not match its selected grammar")
            for key in ("source", "encoded"):
                start = row[key]
                if (
                    type(start) is not int
                    or not previous[key] <= start
                    or start + len(row["value"]) > value[key + "_chars"]
                ):
                    raise ValueError(
                        "Overlapping or out-of-range protected literal context"
                    )
                previous[key] = start + len(row["value"])
        return value

    def transform(
        self, text: str, mode: Mode, context: dict[str, Any]
    ) -> tuple[str, int, int, int, dict[str, Any]]:
        selected = self.spans(text, mode)
        pieces: list[str] = []
        rows = []
        cursor = output_chars = size = substitutions = literals = escapes = 0
        for item in [*selected, LiteralSpan(len(text), len(text), "", "")]:
            converted = self.translator.transform(text[cursor : item.start], mode)
            substitutions += converted.substitutions
            literals += converted.literals
            escapes += converted.escapes
            if item.value:
                rows.append(
                    {
                        "kind": item.kind,
                        "source": item.start,
                        "encoded": output_chars + len(converted.text),
                        "value": item.value,
                    }
                )
            pieces.extend((converted.text, item.value))
            output_chars += len(converted.text) + len(item.value)
            size += len(converted.text.encode("utf-8")) + len(
                item.value.encode("utf-8")
            )
            if size > self.limit or output_chars > MAX_CHARS:
                raise ValueError("Protected translation exceeds its output limit")
            cursor = item.end
        result = "".join(pieces)
        if mode == "forward":
            saved = {
                "kind": "bamboozle.text-literals",
                "schema_version": 1,
                "source_chars": len(text),
                "encoded_chars": len(result),
                "source_sha256": sha256(text.encode("utf-8")),
                "encoded_sha256": sha256(result.encode("utf-8")),
                "spans": rows,
            }
        else:
            saved = {}
            if context and sha256(text.encode("utf-8")) == context["encoded_sha256"]:
                actual = [
                    {
                        "kind": item.kind,
                        "source": row["encoded"],
                        "encoded": row["source"],
                        "value": item.value,
                    }
                    for item, row in zip(selected, rows, strict=True)
                ]
                if (
                    actual != context["spans"]
                    or len(text) != context["encoded_chars"]
                    or len(result) != context["source_chars"]
                    or sha256(result.encode("utf-8")) != context["source_sha256"]
                ):
                    raise ValueError(
                        "Protected text disagrees with its retained exact span context"
                    )
        return result, substitutions, literals, escapes, saved
