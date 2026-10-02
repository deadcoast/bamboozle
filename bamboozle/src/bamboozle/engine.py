"""Single-pass, collision-protected text translation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from bamboozle.dictionary import LITERAL_PREFIX, Dictionary
from bamboozle.utils import MAX_FILE_BYTES

Mode = Literal["forward", "reverse"]


@dataclass(frozen=True)
class Translation:
    text: str
    substitutions: int
    literals: int


def match_case(original: str, target: str) -> str:
    if original.isupper():
        return target.upper()
    if original.istitle():
        return target.title()
    if original == original.lower():
        return target.lower()
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
    ):
        if max_output_chars <= 0:
            raise ValueError("Translation limit must be positive")
        self.max_output_chars = max_output_chars
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
        self._prefix = re.compile(re.escape(LITERAL_PREFIX), re.IGNORECASE)

    def _pattern(self, text: str, mode: Mode) -> re.Pattern[str]:
        # re's \w omits combining marks that continue Python identifiers.
        extras = "".join(
            sorted(
                char
                for char in set(text)
                if not re.match(r"\w", char) and ("a" + char).isidentifier()
            )
        )
        boundary = r"[\w" + re.escape(extras) + "]"
        terms = (
            set(self.dictionary) | set(self.dictionary.values())
            if mode == "forward"
            else set(self.dictionary.values())
        )
        pattern = (
            "(?<!" + boundary + ")(" + _alternatives(terms) + ")(?!" + boundary + ")"
        )
        if mode == "reverse":
            pattern = self._literal.pattern + "|" + pattern
        return re.compile(pattern, re.IGNORECASE)

    def _transform(self, text: str, mode: Mode) -> Translation:
        length = len(text)
        if length > self.max_output_chars:
            raise ValueError("Translation exceeds the configured size limit")
        for prefix in self._prefix.finditer(text):
            if mode == "forward":
                raise ValueError("Input already contains reserved literal markers")
            if self._literal.match(text, prefix.start()) is None:
                raise ValueError("Damaged or unrecognized literal marker")
        substitutions = literals = 0

        def bounded(replacement: str, original: str) -> str:
            nonlocal length
            length += len(replacement) - len(original)
            if length > self.max_output_chars:
                raise ValueError("Translation exceeds the configured size limit")
            return replacement

        def replace(match: re.Match[str]) -> str:
            nonlocal substitutions, literals
            original = match.group(0)
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
            return bounded(match_case(original, mapping[folded]), original)

        result = self._pattern(text, mode).sub(replace, text)
        return Translation(result, substitutions, literals)

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
