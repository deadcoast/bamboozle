"""Explicit historical-ledger migration through a verified retained input pair."""

from __future__ import annotations

import json
import re
from pathlib import Path

from bamboozle.dictionary import LITERAL_PREFIX, Dictionary
from bamboozle.engine import Mode, Translator, _alternatives
from bamboozle.restoration import ReviewedFilePlan
from bamboozle.service import _ledger, ledger_path
from bamboozle.utils import json_bytes, parse_json, sha256
from bamboozle.workspace import Workspace


class _LegacyV2(Translator):
    """The retained standalone v1/v2 engine treated underscores as separators."""

    def __init__(self, mapping: Dictionary, limit: int):
        super().__init__(mapping, max_output_chars=limit)
        if any(not s.isascii() or not t.isascii() for s, t in mapping.items()):
            raise ValueError("Standalone historical dictionaries require ASCII terms")
        self._literal = re.compile(
            re.escape(LITERAL_PREFIX)
            + "(?P<literal>(?ai:"
            + _alternatives(set(mapping.values()))
            + "))__"
        )
        self._prefix = re.compile(re.escape(LITERAL_PREFIX), re.IGNORECASE | re.ASCII)

    def _pattern(self, text: str, mode: Mode) -> re.Pattern[str]:
        extras = "".join(
            sorted(
                char
                for char in set(text)
                if char != "_" and not char.isalnum() and ("a" + char).isidentifier()
            )
        )
        boundary = (
            r"[^\W_]" if not extras else r"(?:[^\W_]|[" + re.escape(extras) + "])"
        )
        terms = (
            set(self.dictionary) | set(self.dictionary.values())
            if mode == "forward"
            else set(self.dictionary.values())
        )
        pattern = (
            "(?<!"
            + boundary
            + ")(?ai:"
            + _alternatives(terms)
            + ")(?!"
            + boundary
            + ")"
        )
        return re.compile(
            self._literal.pattern + "|" + pattern if mode == "reverse" else pattern
        )


def _global_forward(
    text: str, mapping: Dictionary, limit: int
) -> tuple[str, list[str]]:
    if LITERAL_PREFIX.casefold() in text.casefold():
        raise ValueError("Historical original contains reserved markers")
    literals = []

    def substitute(pattern: str, replacement, value: str) -> str:
        size = len(value.encode("utf-8"))

        def bounded(match):
            nonlocal size
            result = replacement(match)
            size += len(result.encode("utf-8")) - len(match.group(0).encode("utf-8"))
            if size > limit:
                raise ValueError("Historical replay exceeds the workspace file limit")
            return result

        return re.sub(pattern, bounded, value, flags=re.IGNORECASE)

    for target in sorted(set(mapping.values()), key=lambda s: (-len(s), s)):
        pattern = r"(?<!\w)" + re.escape(target) + r"(?!\w)"
        if re.search(pattern, text, re.IGNORECASE):
            literals.append(target)
            text = substitute(
                pattern, lambda m: LITERAL_PREFIX + m.group(0) + "__", text
            )
    for source in sorted(mapping, key=lambda s: (-len(s), s)):
        target = mapping[source]

        def case(match, target=target):
            return (
                target.upper()
                if match.group(0).isupper()
                else target.title()
                if match.group(0).istitle()
                else target.lower()
            )

        text = substitute(r"(?<!\w)" + re.escape(source) + r"(?!\w)", case, text)
    return text, literals


def prepare_migration(
    workspace: Workspace,
    source: str | Path,
    original: str | Path,
    *,
    ledger: str | Path,
    output: str | Path | None = None,
    dictionary: str | Path | None = None,
    recover_metadata: bool = False,
) -> ReviewedFilePlan:
    encoded_path, original_path, historical = (
        workspace.resolve(p) for p in (source, original, ledger)
    )
    if len({encoded_path, original_path, historical}) != 3 or any(
        p.suffix.casefold() == ".key" for p in (encoded_path, original_path, historical)
    ):
        raise ValueError(
            "Migration requires separate original, encoded, and ledger files; no keys"
        )
    encoded, retained, old = (
        workspace.read(p) for p in (encoded_path, original_path, historical)
    )
    inputs = {
        encoded_path: sha256(encoded),
        original_path: sha256(retained),
        historical: sha256(old),
    }
    record = parse_json(old)
    if (
        not isinstance(record, dict)
        or record.get("original_file") != original_path.name
    ):
        raise ValueError("Historical ledger does not name the supplied original")
    explicit = None
    if dictionary is not None:
        path = workspace.resolve(dictionary)
        raw = workspace.read(path)
        inputs[path] = sha256(raw)
        explicit = Dictionary.from_document(parse_json(raw))
    normalized = False
    if type(record.get("version")) is int and record["version"] in (1, 2):
        profile = "standalone-v" + str(record["version"])
        if record.get("encoded_file") != encoded_path.name or not isinstance(
            record.get("dictionary"), dict
        ):
            raise ValueError("Historical ledger does not match the encoded file")
        mapping = Dictionary(record["dictionary"])
        if explicit is not None and dict(explicit) != dict(mapping):
            raise ValueError("Explicit dictionary differs from the historical snapshot")
        replay = (
            _LegacyV2(mapping, workspace.max_bytes)
            .forward(retained.decode("utf-8"))
            .encode("utf-8")
        )
        if record["version"] == 2:
            historical_digest = sha256(
                json.dumps(dict(mapping), sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if any(
                record.get(name) != digest
                for name, digest in (
                    ("dictionary_sha256", historical_digest),
                    ("original_sha256", sha256(retained)),
                    ("encoded_sha256", sha256(encoded)),
                )
            ):
                raise ValueError(
                    "Historical integrity digests do not match the supplied pair"
                )
        if replay != encoded:
            raise ValueError(
                "Historical original and encoded file do not form an exact pair; edited or mismatched inputs cannot be migrated"
            )
    elif "version" not in record and set(record) == {
        "original_file",
        "track_literals",
        "timestamp",
    }:
        profile = "global-ledger"
        if explicit is None:
            raise ValueError(
                "Global ledger migration requires an explicit --dict snapshot"
            )
        mapping = explicit
        source_text, encoded_text = retained.decode("utf-8"), encoded.decode("utf-8")
        # The original global script used universal-newline text I/O. Its pair
        # proof is textual; the new native encoding preserves supplied raw bytes.
        normalize = lambda text: text.replace("\r\n", "\n").replace("\r", "\n")
        replay_text, tracked = _global_forward(
            normalize(source_text), mapping, workspace.max_bytes
        )
        if replay_text != normalize(encoded_text):
            raise ValueError(
                "Global ledger inputs do not reproduce the historical encoding"
            )
        if (
            not isinstance(record["track_literals"], list)
            or any(not isinstance(t, str) for t in record["track_literals"])
            or sorted(record["track_literals"]) != sorted(tracked)
        ):
            raise ValueError(
                "Global ledger literal evidence does not match the original"
            )
        normalized = (
            normalize(source_text) != source_text
            or normalize(encoded_text) != encoded_text
        )
    else:
        raise ValueError(
            "Unsupported historical ledger; expected standalone v1/v2 or global ledger"
        )
    result = (
        Translator(mapping, max_output_chars=workspace.max_bytes)
        .forward(retained.decode("utf-8"))
        .encode("utf-8")
    )
    if len(result) > workspace.max_bytes:
        raise ValueError("Migrated encoding exceeds the workspace file limit")
    destination = workspace.resolve(
        output
        if output is not None
        else encoded_path.with_stem(encoded_path.stem + "_migrated"),
        write=True,
    )
    target_ledger = workspace.resolve(ledger_path(destination), write=True)
    if destination in inputs or target_ledger in inputs:
        raise ValueError("Migration outputs must be separate from every retained input")
    document = parse_json(_ledger(retained, result, mapping, workspace.max_bytes))
    # A reproducible review must not depend on when a second CLI process starts.
    document["created"] = "historical-migration"
    document["migration"] = {
        "profile": profile,
        "inputs": {
            str(p.relative_to(workspace.root)): digest for p, digest in inputs.items()
        },
        "historical_newlines_normalized": normalized,
        "provenance": "Consistency with supplied original and historical encoding; not authenticated history",
    }
    metadata = json_bytes(document)
    if len(metadata) > workspace.max_bytes:
        raise ValueError("Migrated ledger exceeds the workspace file limit")
    plan = ReviewedFilePlan(
        workspace,
        destination,
        target_ledger,
        inputs,
        result,
        metadata,
        {
            "operation": "migrate_ledger",
            "historical_profile": profile,
            "historical_encoded_sha256": sha256(encoded),
            "original_sha256": sha256(retained),
            "dictionary_sha256": mapping.fingerprint,
            "encoding_changed": result != encoded,
            "historical_newlines_normalized": normalized,
            "next_action": "Review the new native encoding before using it for edits; historical files and ledger remain unchanged",
        },
    )
    plan.recheck(recover_metadata=recover_metadata)
    return plan
