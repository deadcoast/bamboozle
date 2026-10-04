"""Immutable, versioned dictionaries for reversible terminology translation."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bamboozle.utils import atomic_write, json_bytes, parse_json, read_bytes, sha256

LITERAL_PREFIX = "__SCM_LIT_"
CASE_PREFIX = "__SCM_CASE_"
PROFILES = ("default", "profanity")


class Dictionary(Mapping[str, str]):
    """A one-to-one mapping with an explicit schema and stable content digest."""

    def __init__(self, entries: Mapping[str, str], version: str = "1"):
        if not isinstance(version, str) or not version.strip():
            raise ValueError("Dictionary version must be a nonempty string")
        if not isinstance(entries, Mapping):
            raise TypeError("Dictionary entries must map strings to strings")
        copied = dict(entries)
        if len(copied) > 10000:
            raise ValueError("Dictionary exceeds the 10000-entry limit")
        sources: set[str] = set()
        targets: set[str] = set()
        for source, target in copied.items():
            for term in (source, target):
                if not isinstance(term, str) or not term or term != term.strip():
                    raise ValueError(
                        "Terms must be nonempty strings without surrounding whitespace"
                    )
                if len(term) > 512:
                    raise ValueError("Dictionary terms cannot exceed 512 characters")
                if term != term.lower() or any(char in term for char in "\r\n\x00"):
                    raise ValueError(
                        "Terms must use lowercase text without newlines or NUL"
                    )
                if any(
                    prefix.casefold() in term.casefold()
                    for prefix in (LITERAL_PREFIX, CASE_PREFIX)
                ):
                    raise ValueError("Terms cannot contain reserved marker prefixes")
            folded_source, folded_target = source.casefold(), target.casefold()
            if folded_source in sources or folded_target in targets:
                raise ValueError("Dictionary sources and targets must each be unique")
            sources.add(folded_source)
            targets.add(folded_target)
        if sources & targets:
            raise ValueError(
                "Dictionary source and target vocabularies must be disjoint"
            )
        self._entries = MappingProxyType(copied)
        self._version = version

    @property
    def version(self) -> str:
        return self._version

    def __getitem__(self, key: str) -> str:
        return self._entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mapping_version": self.version,
            "entries": dict(self),
        }

    @property
    def fingerprint(self) -> str:
        return sha256(json_bytes(self.document()))

    @classmethod
    def from_document(cls, document: Any) -> Dictionary:
        if not isinstance(document, dict):
            raise TypeError("Dictionary must be a JSON object")
        if isinstance(document.get("entries"), dict):
            if set(document) != {"schema_version", "mapping_version", "entries"}:
                raise ValueError("Unexpected dictionary schema fields")
            if (
                type(document["schema_version"]) is not int
                or document["schema_version"] != 1
            ):
                raise ValueError("Unsupported dictionary schema version")
            return cls(document["entries"], document["mapping_version"])
        return cls(document)

    @classmethod
    def load(cls, path: Path | None = None, *, profile: str = "default") -> Dictionary:
        if profile not in PROFILES:
            raise ValueError(f"Unknown dictionary profile: {profile}")
        if path is not None:
            if profile != "default":
                raise ValueError(
                    "Choose either a custom dictionary or a bundled profile"
                )
            return cls.from_document(parse_json(read_bytes(path)))
        resources = files("bamboozle")
        profanity = cls.from_document(
            parse_json(resources.joinpath("profanity_dict.json").read_bytes())
        )
        if profile == "profanity":
            return profanity
        original = cls.from_document(
            parse_json(resources.joinpath("cipher_dict.json").read_bytes())
        )
        entries = dict(original)
        for source, target in profanity.items():
            if source in entries and entries[source] != target:
                raise ValueError(f"Bundled dictionaries disagree on source: {source}")
            entries[source] = target
        return cls(entries, version="2")

    def save(self, path: Path) -> None:
        """Write a versioned snapshot without replacing an existing file."""
        atomic_write(path, json_bytes(self.document()))
