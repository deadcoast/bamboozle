"""Reviewed restoration of edited translations with retained local provenance."""

from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bamboozle.formats import DocumentCodec
from bamboozle.return_validation import inspect_return
from bamboozle.service import (
    OperationError,
    _ledger_dictionary,
    _restore,
    ledger_format,
    ledger_marker_version,
    ledger_path,
)
from bamboozle.utils import (
    PublicationError,
    atomic_write,
    json_bytes,
    parse_json,
    sha256,
)
from bamboozle.workspace import Workspace

MAX_CHOICES = 10_000
MAX_CONFLICTS = 100
MAX_DIFF_BYTES = 65_536
RECEIPT_KIND = "vigiles.edited-restoration"


def _lines(text: str) -> list[str]:
    return re.split(r"(?<=\n)|(?<=\r)(?!\n)", text)


def receipt_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.vigiles-restore.json")


def _diff(before: bytes, after: bytes) -> dict[str, Any]:
    """Bound both diff work and returned text; hashes always cover complete bytes."""
    if len(before) + len(after) > 262_144:
        return {
            "diff": "",
            "diff_complete": False,
            "diff_reason": "Diff input limit exceeded",
        }
    left, right = (
        before.decode("utf-8").splitlines(True),
        after.decode("utf-8").splitlines(True),
    )
    if len(left) * len(right) > 4_000_000:
        return {
            "diff": "",
            "diff_complete": False,
            "diff_reason": "Diff line comparison limit exceeded",
        }
    chunks: list[str] = []
    total = 0
    for line in difflib.unified_diff(
        left, right, fromfile="retained-original", tofile="restored-edits"
    ):
        size = len(line.encode("utf-8"))
        if total + size > MAX_DIFF_BYTES:
            return {
                "diff": "".join(chunks),
                "diff_complete": False,
                "diff_reason": "Diff output limit exceeded",
            }
        chunks.append(line)
        total += size
    return {"diff": "".join(chunks), "diff_complete": True}


@dataclass(frozen=True)
class ReviewedFilePlan:
    workspace: Workspace
    output: Path
    metadata_path: Path
    inputs: dict[Path, str]
    result: bytes | None = field(repr=False)
    metadata: bytes | None = field(repr=False)
    details: dict[str, Any]

    @property
    def review_sha256(self) -> str:
        return sha256(
            json_bytes(
                {
                    "workshop": str(self.workspace.root),
                    "output": str(self.output.relative_to(self.workspace.root)),
                    "metadata": str(
                        self.metadata_path.relative_to(self.workspace.root)
                    ),
                    "inputs": {
                        str(p.relative_to(self.workspace.root)): digest
                        for p, digest in self.inputs.items()
                    },
                    "output_sha256": sha256(self.result)
                    if self.result is not None
                    else None,
                    "metadata_sha256": sha256(self.metadata)
                    if self.metadata is not None
                    else None,
                }
            )
        )

    def report(self) -> dict[str, Any]:
        return {
            **self.details,
            "schema_version": 1,
            "status": "conflict" if self.result is None else "preview",
            "ok": self.result is not None,
            "output": str(self.output.relative_to(self.workspace.root)),
            "metadata": str(self.metadata_path.relative_to(self.workspace.root)),
            "output_sha256": sha256(self.result) if self.result is not None else None,
            "output_bytes": len(self.result) if self.result is not None else None,
            "review_sha256": self.review_sha256,
            "output_saved": False,
            "metadata_saved": False,
        }

    def recheck(self, *, recover_metadata: bool = False) -> None:
        for path, digest in self.inputs.items():
            if sha256(self.workspace.read(path)) != digest:
                raise ValueError(
                    f"Input changed after review: {path.name}; preview again"
                )
        for path in (
            self.output,
            self.metadata_path,
            ledger_path(self.output),
            receipt_path(self.output),
        ):
            self.workspace.resolve(path, write=True)
            if path.exists():
                if path == self.output and recover_metadata:
                    if self.result is None or self.workspace.read(path) != self.result:
                        raise ValueError(
                            "Retained output differs from the reviewed bytes"
                        )
                else:
                    raise FileExistsError(
                        f"Destination or metadata already exists: {path.name}"
                    )
        if recover_metadata and not self.output.is_file():
            raise FileNotFoundError(
                "Metadata recovery requires the retained reviewed output"
            )

    def apply(
        self, expected_sha256: str, *, recover_metadata: bool = False
    ) -> dict[str, Any]:
        if self.result is None or self.metadata is None:
            raise ValueError("Resolve every restoration conflict before application")
        if expected_sha256 != self.review_sha256:
            raise ValueError("Review digest does not match; inspect a fresh preview")
        self.recheck(recover_metadata=recover_metadata)
        report = self.report()
        report["output_saved"] = recover_metadata
        try:
            if not recover_metadata:
                try:
                    atomic_write(self.output, self.result)
                except PublicationError:
                    report["output_saved"] = True
                    raise
                report["output_saved"] = True
            # Retain a published output if an input changed before metadata publication.
            for path, digest in self.inputs.items():
                if sha256(self.workspace.read(path)) != digest:
                    raise ValueError(
                        "Input changed during publication; retained output needs inspection"
                    )
            if self.workspace.read(self.output) != self.result:
                raise ValueError("Output changed before metadata publication")
            try:
                atomic_write(
                    self.workspace.resolve(self.metadata_path, write=True),
                    self.metadata,
                )
            except PublicationError:
                report["metadata_saved"] = True
                raise
            report["metadata_saved"] = True
        except (OSError, ValueError) as exc:
            report.update(status="error", error=str(exc))
            raise OperationError(str(exc), report) from exc
        report.update(status="metadata_recovered" if recover_metadata else "saved")
        return report


def prepare_restore(
    workspace: Workspace,
    source: str | Path,
    original: str | Path,
    *,
    output: str | Path | None = None,
    ledger: str | Path | None = None,
    terms: str = "review",
    decisions: str | Path | None = None,
    recover_metadata: bool = False,
) -> ReviewedFilePlan:
    """Use exact baseline lines automatically; require intent for changed-line terms."""
    if terms not in {"review", "translate", "literal"}:
        raise ValueError("Term policy must be review, translate, or literal")
    source_path, original_path = workspace.resolve(source), workspace.resolve(original)
    if source_path == original_path or any(
        p.suffix.casefold() == ".key" for p in (source_path, original_path)
    ):
        raise ValueError("Restoration requires separate text inputs, never key files")
    ledger_file = workspace.resolve(
        ledger if ledger is not None else ledger_path(source_path)
    )
    raw, retained, metadata = (
        workspace.read(source_path),
        workspace.read(original_path),
        workspace.read(ledger_file),
    )
    record = parse_json(metadata)
    if not isinstance(record, dict):
        raise TypeError("Translation ledger must be an object")
    mapping, record = _ledger_dictionary(metadata)
    rules, context = ledger_format(record)
    structured = rules.format != "text"
    if structured and (terms != "translate" or decisions is not None):
        raise ValueError(
            "Structured restoration uses its saved field/format policy: select --terms translate and preserve literal markers; line-based decisions apply only to text format"
        )
    marker_version = ledger_marker_version(record)
    codec = DocumentCodec(
        mapping, rules, limit=workspace.max_bytes, marker_version=marker_version
    )
    translator = codec.translator
    baseline = codec.transform(retained.decode("utf-8")).text.encode("utf-8")
    # Shared strict verification binds original, generated baseline and mapping.
    if _restore(baseline, metadata, workspace.max_bytes) != retained:
        raise ValueError("Supplied original does not match the retained ledger")
    inputs = {
        source_path: sha256(raw),
        original_path: sha256(retained),
        ledger_file: sha256(metadata),
    }
    choices: dict[str, str] = {}
    if decisions is not None:
        path = workspace.resolve(decisions)
        data = workspace.read(path)
        inputs[path] = sha256(data)
        document = parse_json(data)
        if (
            not isinstance(document, dict)
            or set(document) != {"schema_version", "edited_sha256", "choices"}
            or type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or document["edited_sha256"] != sha256(raw)
            or not isinstance(document["choices"], dict)
            or len(document["choices"]) > MAX_CHOICES
            or any(
                choice not in ("translate", "literal")
                for choice in document["choices"].values()
            )
        ):
            raise ValueError(
                "Invalid or stale decisions; bind choices to the edited SHA-256"
            )
        choices = document["choices"]
    unchanged = Counter(_lines(baseline.decode("utf-8")))
    edited = raw.decode("utf-8")
    if not structured:
        translator.validate_markers(edited)
    pieces: list[str] = (
        [codec.transform(edited, "reverse", context=context).text] if structured else []
    )
    conflicts: list[dict[str, Any]] = []
    conflict_count = 0
    resolved: dict[str, str] = {}
    used: set[str] = set()
    result_size = 0
    pattern = translator._pattern(edited, "reverse")
    for number, line in enumerate([] if structured else _lines(edited), 1):
        known = unchanged[line] > 0
        if known:
            unchanged[line] -= 1
        line_bytes = len(line.encode("utf-8"))

        def replace(match, *, number=number, known=known, prefix_size=result_size):
            nonlocal conflict_count, line_bytes

            def bounded(value: str) -> str:
                nonlocal line_bytes
                line_bytes += len(value.encode("utf-8")) - len(
                    match.group(0).encode("utf-8")
                )
                if prefix_size + line_bytes > workspace.max_bytes:
                    raise ValueError("Restored output exceeds the workspace file limit")
                return value

            if match.group("literal") is not None:
                return bounded(match.group("literal"))
            if match.groupdict().get("escape_hex") is not None:
                return bounded(translator.marker_at(match.group(0), 0).decoded)
            if match.group("case_term") is not None:
                return bounded(translator.reverse(match.group(0)))
            location = f"{number}:{match.start() + 1}"
            decision = "translate" if known else choices.get(location, terms)
            if not known:
                used.add(location)
                if len(used) > MAX_CHOICES:
                    raise ValueError("Edited file exceeds the 10000 changed-term limit")
            reason = "Choose whether this changed-line term is translated vocabulary or a new literal"
            if decision == "literal":
                resolved[location] = decision
                return match.group(0)
            try:
                converted = translator.reverse(match.group(0))
            except ValueError as exc:
                converted = None
                reason = str(exc) + "; preserve as literal or edit the term"
            if decision == "translate" and converted is not None:
                if not known:
                    resolved[location] = decision
                return bounded(converted)
            conflict_count += 1
            if len(conflicts) < MAX_CONFLICTS:
                conflicts.append(
                    {
                        "id": location,
                        "line": number,
                        "column": match.start() + 1,
                        "term": match.group(0),
                        "translation": converted,
                        "reason": reason,
                        "choices": ["translate", "literal"]
                        if converted is not None
                        else ["literal"],
                    }
                )
            return match.group(0)

        rendered = pattern.sub(replace, line)
        result_size += len(rendered.encode("utf-8"))
        if result_size > workspace.max_bytes:
            raise ValueError("Restored output exceeds the workspace file limit")
        pieces.append(rendered)
    if choices.keys() - used:
        raise ValueError(
            "Decisions contain unknown or unchanged-line positions; preview again"
        )
    destination = workspace.resolve(
        output
        if output is not None
        else source_path.with_stem(source_path.stem + "_ready"),
        write=True,
    )
    receipt = workspace.resolve(receipt_path(destination), write=True)
    if destination in inputs or receipt in inputs:
        raise ValueError(
            "Output and receipt must be separate from every retained input"
        )
    result = "".join(pieces).encode("utf-8") if conflict_count == 0 else None
    provenance = {
        "kind": RECEIPT_KIND,
        "schema_version": 1,
        "inputs": {
            str(p.relative_to(workspace.root)): digest for p, digest in inputs.items()
        },
        "original": str(original_path.relative_to(workspace.root)),
        "edited": str(source_path.relative_to(workspace.root)),
        "ledger": str(ledger_file.relative_to(workspace.root)),
        "baseline_encoded_sha256": sha256(baseline),
        "dictionary_sha256": mapping.fingerprint,
        "output": str(destination.relative_to(workspace.root)),
        "output_sha256": sha256(result) if result is not None else None,
        "term_policy": terms,
        **({"document_rules": rules.document()} if structured else {}),
        "resolved_terms": resolved,
        "provenance": "Local consistency with retained original and ledger; not authenticated authorship",
    }
    receipt_bytes = json_bytes(provenance) if result is not None else None
    if receipt_bytes is not None and len(receipt_bytes) > workspace.max_bytes:
        raise ValueError("Restoration receipt exceeds the workspace file limit")
    details = {
        "operation": "restore_edits",
        "return_validation": inspect_return(
            raw,
            mapping,
            baseline=baseline,
            rules=rules,
            context=context,
            limit=workspace.max_bytes,
            marker_version=marker_version,
        ),
        "input": provenance["edited"],
        "original": provenance["original"],
        "edited_sha256": sha256(raw),
        "baseline_encoded_sha256": sha256(baseline),
        "dictionary_sha256": mapping.fingerprint,
        "term_policy": terms,
        **({"document_rules": rules.document()} if structured else {}),
        "conflict_count": conflict_count,
        "conflicts": conflicts,
        "omitted_conflicts": max(0, conflict_count - len(conflicts)),
        "resolved_term_count": len(resolved),
        "edited": raw != baseline,
        **(
            _diff(retained, result)
            if result is not None
            else {"diff": "", "diff_complete": False}
        ),
    }
    plan = ReviewedFilePlan(
        workspace, destination, receipt, inputs, result, receipt_bytes, details
    )
    plan.recheck(recover_metadata=recover_metadata)
    return plan


def verify_restoration(workspace: Workspace, source: str | Path) -> dict[str, Any]:
    path = workspace.resolve(source)
    receipt = workspace.read(receipt_path(path))
    record = parse_json(receipt)
    if (
        not isinstance(record, dict)
        or record.get("kind") != RECEIPT_KIND
        or type(record.get("schema_version")) is not int
        or record["schema_version"] != 1
        or record.get("output") != path.relative_to(workspace.root).as_posix()
        and record.get("output") != str(path.relative_to(workspace.root))
        or not isinstance(record.get("inputs"), dict)
        or not 3 <= len(record["inputs"]) <= 4
    ):
        raise ValueError("Unsupported restoration receipt")
    inputs = record["inputs"]
    for name in (record.get("original"), record.get("edited"), record.get("ledger")):
        if not isinstance(name, str) or name not in inputs:
            raise ValueError("Restoration receipt is missing required provenance")
    observed = {name: sha256(workspace.read(name)) for name in inputs}
    if observed != inputs:
        raise ValueError("Retained restoration inputs changed")
    result = workspace.read(path)
    if sha256(result) != record.get("output_sha256"):
        raise ValueError("Restored output changed")
    for name, digest in observed.items():
        if sha256(workspace.read(name)) != digest:
            raise ValueError("Restoration evidence changed during verification")
    if workspace.read(receipt_path(path)) != receipt or workspace.read(path) != result:
        raise ValueError("Restoration output or receipt changed during verification")
    return {
        "schema_version": 1,
        "status": "verified",
        "output_sha256": sha256(result),
        "receipt_sha256": sha256(receipt),
        "input_count": len(inputs),
        "provenance": "local_consistency",
    }
