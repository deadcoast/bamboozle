"""Bounded operator diagnostics for edited neutral documents; never a translator."""

from __future__ import annotations

import bisect
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bamboozle.dictionary import LITERAL_PREFIX, Dictionary
from bamboozle.formats import DocumentCodec, FormatRules, TextRegion
from bamboozle.markers import CURRENT, LEGACY
from bamboozle.service import (
    _ledger_dictionary,
    _restore,
    ledger_format,
    ledger_marker_version,
    ledger_path,
)
from bamboozle.utils import MAX_FILE_BYTES, json_bytes, parse_json, sha256
from bamboozle.workspace import Workspace

VERSION = "2"
MAX_ANALYSIS_CHARS = 524288
MAX_REGIONS = 8192
MAX_FINDINGS = 500
NEWLINE = re.compile(r"\r\n|\r|\n")
LIMITS = (
    "Diagnostics are operator evidence, not inferred intent or semantic correctness. "
    "Changed-line target words can be vocabulary or literals. Missing terms/markers "
    "may be intentional deletion. Variant hints cover simple ASCII word suffixes "
    "and underscore identifiers only; synonyms, arbitrary spelling changes, multiword "
    "phrase variants and other languages are not inferred. "
    "Protected-content changes require review, not automatic rejection."
)


@dataclass
class Findings:
    offset: int
    limit: int
    counts: Counter[str] = field(default_factory=Counter)
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0

    def add(self, code: str, message: str, **evidence: Any) -> None:
        index = self.total
        self.total += 1
        self.counts[code] += 1
        if self.offset <= index < self.offset + self.limit:
            self.items.append(
                {"index": index, "code": code, "message": message, **evidence}
            )


def _excerpt(value: str) -> dict[str, Any]:
    return {
        "text": value[:160],
        "complete": len(value) <= 160,
        "sha256": sha256(value.encode("utf-8")),
    }


class Locations:
    def __init__(self, text: str):
        self.starts = [0, *(match.end() for match in NEWLINE.finditer(text))]

    def at(self, position: int) -> dict[str, int]:
        index = bisect.bisect_right(self.starts, position) - 1
        return {
            "offset": position,
            "line": index + 1,
            "column": position - self.starts[index] + 1,
        }


def _lines(value: str) -> list[str]:
    # Same baseline-line intent boundary as reviewed single-file restoration.
    return re.split(r"(?<=\n)|(?<=\r)(?!\n)", value)


def _units(regions: list[TextRegion]) -> Counter[tuple[str | None, str]]:
    return Counter(
        (region.pointer, line) for region in regions for line in _lines(region.value)
    )


def _variant_bases(word: str) -> set[str]:
    """Candidates for diagnostics only; these never become reverse mappings."""
    if not 4 <= len(word) <= 520 or re.fullmatch(r"[a-z]+(?:_[a-z]+)*", word) is None:
        return set()
    candidates = set()
    for suffix in ("s", "es", "ed", "ing", "er", "ers", "est", "ly"):
        if word.endswith(suffix):
            base = word[: -len(suffix)]
            candidates.add(base)
            if suffix in {"ed", "ing", "er", "ers", "est"}:
                candidates.add(base + "e")
                if len(base) > 2 and base[-1] == base[-2]:
                    candidates.add(base[:-1])
    for suffix in ("ies", "ied", "ier", "iest"):
        if word.endswith(suffix):
            candidates.add(word[: -len(suffix)] + "y")
    return {base for base in candidates if 3 <= len(base) <= 512}


def _inspect_terms(
    codec: DocumentCodec,
    text: str,
    regions: list[TextRegion],
    known: Counter[tuple[str | None, str]],
    findings: Findings | None,
) -> tuple[Counter[str], Counter[str]]:
    translator = codec.translator
    if codec.python is not None:
        selected_terms: Counter[str] = Counter()
        for region in regions:
            name = codec.python.reverse.get(region.value)
            if name is not None:
                selected_terms[translator.dictionary[name.casefold()].casefold()] += 1
        return selected_terms, Counter()
    sources = {term.casefold(): term for term in translator.dictionary}
    targets = {term.casefold(): term for term in translator.dictionary.values()}
    vocabulary = sources | targets
    markers: Counter[str] = Counter()
    terms: Counter[str] = Counter()
    document_locations = Locations(text)
    for region in regions:
        value_locations = Locations(region.value)

        def location(
            position: int,
            region: TextRegion = region,
            value_locations: Locations = value_locations,
        ) -> dict[str, Any]:
            if region.pointer is not None:
                return {
                    "pointer": region.pointer,
                    "token": document_locations.at(region.start),
                    "value": value_locations.at(position),
                }
            return document_locations.at(region.start + position)

        # Mask only valid markers; malformed prefixes remain available for refusal.
        masked = list(region.value)
        position = 0
        while (prefix := translator._prefix.search(region.value, position)) is not None:
            position = prefix.end()
            try:
                marker = translator.marker_at(region.value, prefix.start())
            except ValueError as exc:
                if findings is not None:
                    findings.add(
                        "invalid_marker",
                        str(exc),
                        location=location(prefix.start()),
                        marker=_excerpt(
                            region.value[prefix.start() : prefix.start() + 160]
                        ),
                    )
                continue
            if translator.marker_version != LEGACY:
                position = marker.end
            markers[region.value[marker.start : marker.end]] += 1
            if marker.term is not None:
                terms[marker.term.casefold()] += 1
            masked[marker.start : marker.end] = " " * (marker.end - marker.start)
        visible = "".join(masked)
        # Respect Unicode identifier continuations exactly as the cipher does.
        extras = "".join(
            sorted(
                char
                for char in set(visible)
                if not re.match(r"\w", char) and ("a" + char).isidentifier()
            )
        )
        words = re.compile(r"[\w" + re.escape(extras) + r"]+")
        position = 0
        for line in _lines(region.value):
            key = (region.pointer, line)
            unchanged = known[key] > 0
            if unchanged:
                known[key] -= 1
            active = visible[position : position + len(line)]
            occupied = bytearray(len(active))
            for match in translator._pattern(active, "forward").finditer(active):
                folded = match.group().casefold()
                occupied[match.start() : match.end()] = b"\1" * len(match.group())
                if folded in targets:
                    terms[folded] += 1
                if findings is None or unchanged:
                    continue
                evidence = {
                    "location": location(position + match.start()),
                    "term": match.group(),
                }
                if folded in targets:
                    findings.add(
                        "ambiguous_target",
                        "Changed-line unmarked target will reverse-map; preserve with a literal marker if meant literally.",
                        **evidence,
                        source_term=translator._reverse[folded],
                        literal_marker=f"{LITERAL_PREFIX}{match.group()}__",
                    )
                elif folded in sources:
                    findings.add(
                        "source_term",
                        "Source vocabulary appeared in changed neutral text; it is not reverse-translated.",
                        **evidence,
                        neutral_term=translator.dictionary[sources[folded]],
                    )
            if findings is not None and not unchanged:
                for match in words.finditer(active):
                    if any(occupied[match.start() : match.end()]):
                        continue
                    word = match.group().casefold()
                    if word in vocabulary:
                        continue
                    bases = sorted(_variant_bases(word) & vocabulary.keys())
                    if bases:
                        findings.add(
                            "unlisted_variant",
                            "Possible undeclared word form; no mapping is inferred. Use an explicit published form or retain it deliberately.",
                            location=location(position + match.start()),
                            term=match.group(),
                            candidates=[
                                {
                                    "term": vocabulary[base],
                                    "domain": "source"
                                    if base in sources
                                    else "neutral",
                                }
                                for base in bases
                            ],
                        )
            position += len(line)
    return terms, markers


def _protected_json(text: str, rules: FormatRules) -> dict[str, bytes]:
    """Compare actual unselected values/types, ignoring JSON whitespace/escapes."""
    document = parse_json(text.removeprefix("\ufeff"))
    selected = set(rules.pointers)
    values: dict[str, bytes] = {}

    def visit(value: Any, path: str) -> None:
        if path in selected:
            return
        if isinstance(value, dict):
            values[path] = b"object"
            for key, child in value.items():
                visit(child, path + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, list):
            values[path] = b"array"
            for index, child in enumerate(value):
                visit(child, path + f"/{index}")
        else:
            # Unselected escaped surrogates remain opaque to translation. Comparing
            # them must not require that their decoded value is UTF-8 encodable.
            values[path] = json.dumps(value, ensure_ascii=True, allow_nan=False).encode(
                "ascii"
            )

    visit(document, "")
    return values


def _protected_markdown(text: str, regions: list[TextRegion]) -> list[tuple[int, str]]:
    result = []
    cursor = 0
    for region in [*regions, TextRegion(len(text), len(text), "")]:
        if region.start > cursor:
            value = text[cursor : region.start]
            # BOM alone is not a technical-content span.
            if value != "\ufeff":
                result.append((cursor, value))
        cursor = region.end
    return result


def _structure(
    codec: DocumentCodec,
    baseline: str,
    edited: str,
    old: list[TextRegion],
    new: list[TextRegion],
    findings: Findings,
) -> None:
    if codec.rules.format == "yaml":
        from bamboozle.yaml_source import protected

        before_yaml = protected(baseline, codec.rules.pointers)
        after_yaml = protected(edited, codec.rules.pointers)
        if before_yaml != after_yaml:
            findings.add(
                "protected_yaml_changed",
                "Protected YAML content changed; review keys, aliases, tags, comments and unselected data separately from selected values.",
                before=_excerpt(before_yaml),
                after=_excerpt(after_yaml),
            )
    elif codec.rules.format == "json":
        before, after = (
            _protected_json(baseline, codec.rules),
            _protected_json(edited, codec.rules),
        )
        for pointer in sorted(before.keys() | after.keys()):
            left, right = before.get(pointer), after.get(pointer)
            if left != right:
                findings.add(
                    "protected_json_changed",
                    "Unselected JSON value or structure changed; review this deliberate source change separately from translated fields.",
                    pointer=pointer,
                    before=_excerpt(left.decode("utf-8")) if left is not None else None,
                    after=_excerpt(right.decode("utf-8"))
                    if right is not None
                    else None,
                )
    elif codec.rules.format in ("markdown", "python"):
        old_spans, new_spans = (
            _protected_markdown(baseline, old),
            _protected_markdown(edited, new),
        )
        for label, text, spans, others in (
            ("removed", baseline, old_spans, new_spans),
            ("added", edited, new_spans, old_spans),
        ):
            available = Counter(value for _, value in others)
            locations = Locations(text)
            for position, value in spans:
                if available[value]:
                    available[value] -= 1
                else:
                    findings.add(
                        "protected_python_changed"
                        if codec.python is not None
                        else "protected_markdown_changed",
                        "Protected content changed; review this deliberate source edit separately from selected translation regions."
                        if codec.python is not None
                        else "Protected Markdown content changed; review code, links and other technical literals.",
                        change=label,
                        location=locations.at(position),
                        content=_excerpt(value),
                    )
        if [value for _, value in old_spans] != [
            value for _, value in new_spans
        ] and Counter(value for _, value in old_spans) == Counter(
            value for _, value in new_spans
        ):
            findings.add(
                "protected_python_reordered"
                if codec.python is not None
                else "protected_markdown_reordered",
                "Protected Markdown spans changed order; inspect the full document diff.",
            )


def inspect_return(
    edited: bytes,
    dictionary: Dictionary,
    *,
    baseline: bytes | None = None,
    rules: FormatRules | None = None,
    context: dict[str, Any] | None = None,
    limit: int = MAX_FILE_BYTES,
    offset: int = 0,
    max_findings: int = 100,
    marker_version: int = CURRENT,
) -> dict[str, Any]:
    """Return accepted/needs_review/refused without changing bytes or term intent.

    Baseline is the saved *encoded* document, not arbitrary original-domain text.
    Caller binds that baseline to its ledger/view. A new file has no baseline.
    A zero page size is allowed for bounded project-level summary collection.
    """
    if (
        type(offset) is not int
        or offset < 0
        or type(max_findings) is not int
        or not 0 <= max_findings <= MAX_FINDINGS
    ):
        raise ValueError("offset must be nonnegative and max_findings must be 0 to 500")
    codec = DocumentCodec(dictionary, rules, limit=limit, marker_version=marker_version)
    findings = Findings(offset, max_findings)
    inputs = {
        "edited_sha256": sha256(edited),
        "baseline_encoded_sha256": sha256(baseline) if baseline is not None else None,
        "dictionary_sha256": dictionary.fingerprint,
        "document_rules": codec.rules.document(),
        "context_sha256": sha256(json_bytes(context or {})),
        "document_byte_limit": limit,
        "marker_version": marker_version,
    }
    refused = False
    complete = True
    decoded_sha: str | None = None
    try:
        text = edited.decode("utf-8")
        old_text = baseline.decode("utf-8") if baseline is not None else ""
        codec.validate_context(context or {})
        regions = codec.regions(text)
        old_regions = codec.regions(old_text) if baseline is not None else []
    except (ValueError, TypeError) as exc:
        findings.add("invalid_document", str(exc))
        refused = True
        complete = False
    else:
        try:
            decoded_sha = sha256(
                codec.transform(text, "reverse", context=context).text.encode("utf-8")
            )
        except (ValueError, TypeError) as exc:
            findings.add("decode_error", str(exc))
            refused = True
        if (
            len(text) + len(old_text) > MAX_ANALYSIS_CHARS
            or len(regions) + len(old_regions) > MAX_REGIONS
        ):
            findings.add(
                "analysis_limit",
                "Terminology/structure analysis limit exceeded; inspect the complete diff manually.",
                max_characters=MAX_ANALYSIS_CHARS,
                max_regions=MAX_REGIONS,
            )
            complete = False
        else:
            old_terms, old_markers = _inspect_terms(
                codec, old_text, old_regions, Counter(), None
            )
            new_terms, new_markers = _inspect_terms(
                codec, text, regions, _units(old_regions), findings
            )
            for marker, count in sorted((old_markers - new_markers).items()):
                findings.add(
                    "marker_removed",
                    "A baseline protected marker is absent or changed; confirm deletion or restore the exact marker.",
                    marker=_excerpt(marker),
                    removed_count=count,
                )
            for term, count in sorted((old_terms - new_terms).items()):
                findings.add(
                    "controlled_term_removed",
                    "Controlled vocabulary occurrence count decreased; inspect possible deletion or terminology drift. Synonyms are not inferred.",
                    neutral_term=term,
                    source_term=codec.translator._reverse[term],
                    removed_count=count,
                )
            if baseline is not None:
                _structure(codec, old_text, text, old_regions, regions, findings)
    status = "refused" if refused else "needs_review" if findings.total else "accepted"
    return {
        "schema_version": 1,
        "validator_version": VERSION,
        "status": status,
        "ok": status == "accepted",
        "decodable": decoded_sha is not None,
        "decoded_sha256": decoded_sha,
        "decode_policy": "saved_mapping_reserved_targets_before_manual_intent_decisions",
        **inputs,
        "validation_sha256": sha256(
            json_bytes({"validator_version": VERSION, **inputs})
        ),
        "baseline_available": baseline is not None,
        "analysis_complete": complete,
        "finding_count": findings.total,
        "counts": dict(sorted(findings.counts.items())),
        "findings": findings.items,
        "offset": offset,
        "next_offset": offset + len(findings.items)
        if max_findings and offset + len(findings.items) < findings.total
        else None,
        "findings_complete": offset == 0 and len(findings.items) == findings.total,
        "limits": LIMITS,
        "output_saved": False,
    }


def validate_return(
    workspace: Workspace,
    source: str | Path,
    original: str | Path,
    *,
    ledger: str | Path | None = None,
    offset: int = 0,
    max_findings: int = 100,
) -> dict[str, Any]:
    """Read-only ledger-bound entry point, including fresh-input checks after analysis."""
    if not 1 <= max_findings <= MAX_FINDINGS:
        raise ValueError("max_findings must be 1 to 500")
    source_path, original_path = workspace.resolve(source), workspace.resolve(original)
    ledger_file = workspace.resolve(
        ledger if ledger is not None else ledger_path(source_path)
    )
    if len({source_path, original_path, ledger_file}) != 3 or any(
        path.suffix.casefold() == ".key"
        for path in (source_path, original_path, ledger_file)
    ):
        raise ValueError(
            "Validation requires separate edited/original/ledger inputs, never key files"
        )
    raw, retained, metadata = (
        workspace.read(source_path),
        workspace.read(original_path),
        workspace.read(ledger_file),
    )
    mapping, record = _ledger_dictionary(metadata)
    rules, context = ledger_format(record)
    marker_version = ledger_marker_version(record)
    codec = DocumentCodec(
        mapping, rules, limit=workspace.max_bytes, marker_version=marker_version
    )
    baseline = codec.transform(retained.decode("utf-8")).text.encode("utf-8")
    if _restore(baseline, metadata, workspace.max_bytes) != retained:
        raise ValueError("Supplied original does not match the retained ledger")
    inputs = {
        source_path: sha256(raw),
        original_path: sha256(retained),
        ledger_file: sha256(metadata),
    }
    report = inspect_return(
        raw,
        mapping,
        baseline=baseline,
        rules=rules,
        context=context,
        limit=workspace.max_bytes,
        offset=offset,
        max_findings=max_findings,
        marker_version=marker_version,
    )
    for path, digest in inputs.items():
        if sha256(workspace.read(path)) != digest:
            raise ValueError(
                "Validation inputs changed during inspection; run a fresh check"
            )
    return {
        **report,
        "operation": "validate_return",
        "input": str(source_path.relative_to(workspace.root)),
        "original": str(original_path.relative_to(workspace.root)),
        "ledger": str(ledger_file.relative_to(workspace.root)),
        "inputs": {
            str(path.relative_to(workspace.root)): digest
            for path, digest in inputs.items()
        },
    }
