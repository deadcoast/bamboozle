"""Single-pass, collision-protected text translation."""

from __future__ import annotations

import re
from collections import Counter, OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from bamboozle.dictionary import CASE_PREFIX, LITERAL_PREFIX, Dictionary
from bamboozle.markers import (
    CURRENT,
    ESCAPE,
    LEGACY,
    quote_prefix,
    unquote_prefix,
    validate_version,
)
from bamboozle.utils import MAX_FILE_BYTES

Mode = Literal["forward", "reverse"]


@dataclass(frozen=True)
class Translation:
    text: str
    substitutions: int
    literals: int
    escapes: int = 0


@dataclass(frozen=True)
class Marker:
    start: int
    end: int
    kind: str
    decoded: str
    term: str | None = None


def match_case(original: str, target: str) -> str:
    if original.isupper():
        return target.upper()
    if original.istitle():
        return target.title()
    if original == original.lower():
        return target.lower()
    if original[:1].isupper() and original[1:] == original[1:].lower():
        return target[:1].upper() + target[1:]
    raise ValueError(
        "Mixed-case terms require an explicit mapping; conversion would lose case"
    )


def _alternatives(terms: Mapping[str, str] | set[str]) -> str:
    return (
        "|".join(
            re.escape(term)
            for term in sorted(terms, key=lambda term: (-len(term), term))
        )
        or r"(?!)"
    )


class Translator:
    def __init__(
        self,
        dictionary: Dictionary | Mapping[str, str],
        *,
        max_output_chars: int = MAX_FILE_BYTES,
        marker_version: int = CURRENT,
    ):
        if max_output_chars <= 0:
            raise ValueError("Translation limit must be positive")
        self.max_output_chars = max_output_chars
        self.marker_version = validate_version(marker_version)
        self.dictionary = (
            dictionary if isinstance(dictionary, Dictionary) else Dictionary(dictionary)
        )
        self._forward = {
            key.casefold(): value for key, value in self.dictionary.items()
        }
        self._reverse = {
            value.casefold(): key for key, value in self.dictionary.items()
        }
        self._literal = re.compile(
            re.escape(LITERAL_PREFIX)
            + "(?P<literal>"
            + _alternatives(set(self.dictionary.values()))
            + ")__",
            re.IGNORECASE,
        )
        self._case = re.compile(
            re.escape(CASE_PREFIX)
            + r"(?P<case_mask>[0-9a-f]{1,128})_(?P<case_term>"
            + _alternatives(set(self.dictionary.values()))
            + r")__",
            re.IGNORECASE,
        )
        self._prefix = re.compile(
            re.escape(LITERAL_PREFIX) + "|" + re.escape(CASE_PREFIX), re.IGNORECASE
        )
        self._terms = {
            "forward": _alternatives(
                set(self.dictionary) | set(self.dictionary.values())
            ),
            "reverse": _alternatives(set(self.dictionary.values())),
        }
        self._patterns: OrderedDict[tuple[str, Mode], re.Pattern[str]] = OrderedDict()

    def _encode_term(self, original: str, source: str, target: str) -> str:
        try:
            rendered = match_case(original, target)
            if match_case(rendered, source) == original:
                return rendered
        except ValueError:
            pass
        # Store only case positions, never the original term. The neutral target
        # remains readable and the marker is a valid identifier for identifier terms.
        if len(original) != len(source) or original.lower() != source:
            raise ValueError("Ambiguous Unicode case folding in matched term")
        mask = 0
        for index, (actual, base) in enumerate(zip(original, source, strict=True)):
            if actual == base:
                continue
            if actual != base.upper() or len(base.upper()) != 1:
                raise ValueError("Case conversion cannot preserve this term exactly")
            mask |= 1 << index
        return f"{CASE_PREFIX}{mask:x}_{target}__"

    def _decode_case(self, match: re.Match[str]) -> str:
        term = match.group("case_term")
        if term.casefold() not in self._reverse:
            raise ValueError(
                "Case marker target must retain its canonical lowercase form"
            )
        source = self._reverse[term.casefold()]
        if term != self.dictionary[source]:
            raise ValueError(
                "Case marker target must retain its canonical lowercase form"
            )
        mask = int(match.group("case_mask"), 16)
        if mask >> len(source):
            raise ValueError("Case marker exceeds the source term length")
        result = []
        for index, char in enumerate(source):
            if mask & (1 << index):
                upper = char.upper()
                if upper == char or len(upper) != 1:
                    raise ValueError("Case marker selects a non-reversible character")
                result.append(upper)
            else:
                result.append(char)
        return "".join(result)

    def marker_at(self, text: str, start: int) -> Marker:
        """Decode one validated marker; callers must advance past its whole span."""
        if (
            self.marker_version == CURRENT
            and (escaped := ESCAPE.match(text, start)) is not None
        ):
            return Marker(
                start, escaped.end(), "escape", unquote_prefix(escaped.group())
            )
        literal = self._literal.match(text, start)
        if literal is not None:
            return Marker(start, literal.end(), "literal", literal.group("literal"))
        case = self._case.match(text, start)
        if case is not None:
            return Marker(
                start,
                case.end(),
                "case",
                self._decode_case(case),
                case.group("case_term"),
            )
        prefix = self._prefix.match(text, start)
        if (
            prefix is not None
            and prefix.group().casefold() == LITERAL_PREFIX.casefold()
        ):
            line = len(re.findall(r"\r\n|\r|\n", text[:start])) + 1
            raise ValueError(f"Damaged literal marker at line {line}")
        raise ValueError("Damaged or unrecognized case marker or prefix escape")

    def validate_markers(self, text: str, mode: Mode = "reverse") -> None:
        if mode == "forward":
            if self.marker_version == LEGACY and self._prefix.search(text):
                raise ValueError(
                    "Input already contains reserved literal or case markers"
                )
            return
        position = 0
        while (prefix := self._prefix.search(text, position)) is not None:
            marker = self.marker_at(text, prefix.start())
            position = marker.end if self.marker_version == CURRENT else prefix.end()

    def _pattern(self, text: str, mode: Mode) -> re.Pattern[str]:
        # re's \w omits combining marks that continue Python identifiers.
        extras = "".join(
            sorted(
                char
                for char in set(text)
                if not re.match(r"\w", char) and ("a" + char).isidentifier()
            )
        )
        key = (extras, mode)
        cached = self._patterns.get(key)
        if cached is not None:
            return cached
        boundary = r"[\w" + re.escape(extras) + "]"
        pattern = "(?<!" + boundary + ")(" + self._terms[mode] + ")(?!" + boundary + ")"
        if mode == "reverse":
            pattern = self._literal.pattern + "|" + self._case.pattern + "|" + pattern
            if self.marker_version == CURRENT:
                pattern = ESCAPE.pattern + "|" + pattern
        elif self.marker_version == CURRENT:
            pattern = "(?P<raw_prefix>" + self._prefix.pattern + ")|" + pattern
        compiled = re.compile(pattern, re.IGNORECASE)
        self._patterns[key] = compiled
        if len(self._patterns) > 32:
            self._patterns.popitem(last=False)
        return compiled

    def scan(self, text: str, *, max_matches: int = 1000) -> dict[str, Any]:
        """Inspect exact dictionary coverage without changing source or normalizing it."""
        if type(max_matches) is not int or not 1 <= max_matches <= 10000:
            raise ValueError("max_matches must be an integer from 1 to 10000")
        if len(text) > self.max_output_chars:
            raise ValueError("Translation exceeds the configured size limit")
        self.validate_markers(text, "forward")
        newline = re.compile(r"\r\n|\r|\n")
        line, line_start, position = 1, 0, 0
        counts: Counter[str] = Counter()
        escaped_prefixes = 0
        matches: list[dict[str, Any]] = []
        for match in self._pattern(text, "forward").finditer(text):
            if match.groupdict().get("raw_prefix") is not None:
                escaped_prefixes += 1
                continue
            term = match.group(0)
            folded = term.casefold()
            if folded in self._reverse:
                continue
            if folded not in self._forward:
                raise ValueError("Ambiguous Unicode case folding in matched term")
            target = self._forward[folded]
            source = self._reverse[target.casefold()]
            counts[source] += 1
            if len(matches) < max_matches:
                for ending in newline.finditer(text, position, match.start()):
                    line += 1
                    line_start = ending.end()
                position = match.end()
                matches.append(
                    {
                        "term": term,
                        "source_term": source,
                        "replacement": self._encode_term(term, source, target),
                        "start": match.start(),
                        "end": match.end(),
                        "line": line,
                        "column": match.start() - line_start + 1,
                    }
                )
        total = sum(counts.values())
        return {
            "matched_terms": total,
            "counts": dict(sorted(counts.items())),
            "matches": matches,
            "matches_complete": total <= max_matches,
            "dictionary_sha256": self.dictionary.fingerprint,
            "mapping_version": self.dictionary.version,
            "marker_version": self.marker_version,
            "escaped_prefixes": escaped_prefixes,
        }

    def _transform(self, text: str, mode: Mode) -> Translation:
        length = len(text)
        if length > self.max_output_chars:
            raise ValueError("Translation exceeds the configured size limit")
        self.validate_markers(text, mode)
        substitutions = literals = escapes = 0

        def bounded(replacement: str, original: str) -> str:
            nonlocal length
            length += len(replacement) - len(original)
            if length > self.max_output_chars:
                raise ValueError("Translation exceeds the configured size limit")
            return replacement

        def replace(match: re.Match[str]) -> str:
            nonlocal substitutions, literals, escapes
            original = match.group(0)
            if mode == "forward" and match.groupdict().get("raw_prefix") is not None:
                escapes += 1
                return bounded(quote_prefix(original), original)
            if mode == "reverse" and match.groupdict().get("escape_hex") is not None:
                escapes += 1
                return bounded(unquote_prefix(original), original)
            if mode == "reverse" and match.groupdict().get("case_term") is not None:
                substitutions += 1
                return bounded(self._decode_case(match), original)
            if mode == "reverse" and match.group("literal") is not None:
                literals += 1
                return bounded(match.group("literal"), original)
            folded = original.casefold()
            if mode == "forward" and folded in self._reverse:
                literals += 1
                return bounded(f"{LITERAL_PREFIX}{original}__", original)
            mapping = self._forward if mode == "forward" else self._reverse
            if folded not in mapping:
                raise ValueError("Ambiguous Unicode case folding in matched term")
            substitutions += 1
            if mode == "forward":
                return bounded(
                    self._encode_term(
                        original,
                        self._reverse[mapping[folded].casefold()],
                        mapping[folded],
                    ),
                    original,
                )
            return bounded(match_case(original, mapping[folded]), original)

        result = self._pattern(text, mode).sub(replace, text)
        return Translation(result, substitutions, literals, escapes)

    def transform(self, text: str, mode: Mode = "forward") -> Translation:
        if mode not in ("forward", "reverse"):
            raise ValueError(f"Unknown translation mode: {mode}")
        result = self._transform(text, mode)
        if mode == "forward" and self._transform(result.text, "reverse").text != text:
            raise ValueError("Ambiguous dictionary: exact round trip would lose text")
        return result

    def forward(self, text: str) -> str:
        return self.transform(text, "forward").text

    def reverse(self, text: str) -> str:
        return self.transform(text, "reverse").text
