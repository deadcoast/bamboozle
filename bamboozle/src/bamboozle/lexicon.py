"""Operator lexicon authoring, deterministic compilation and reviewed publication.

Concepts group explicit forms; the runtime artifact remains an ordinary Dictionary.
No morphology, synonyms or many-to-one reverse mappings are inferred.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bamboozle.dictionary import Dictionary, validate_term
from bamboozle.engine import Translator
from bamboozle.restoration import ReviewedFilePlan, _diff
from bamboozle.utils import atomic_write, json_bytes, parse_json, sha256
from bamboozle.workspace import Workspace

MAX_FORMS = 10_000
MAX_DIAGNOSTICS = 100
IDENTIFIER = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,63}\Z")
KIND = "bamboozle.lexicon-compilation"


@dataclass(frozen=True)
class Analysis:
    dictionary: Dictionary | None
    rows: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    error_count: int

    def report(self) -> dict[str, Any]:
        mapping = self.dictionary
        return {
            "ok": mapping is not None,
            "status": "valid" if mapping is not None else "invalid",
            "forms": len(self.rows),
            "mapping_version": mapping.version if mapping is not None else None,
            "dictionary_sha256": mapping.fingerprint if mapping is not None else None,
            "diagnostics": self.diagnostics,
            "error_count": self.error_count,
            "diagnostics_complete": self.error_count == len(self.diagnostics),
        }


def analyze(document: Any) -> Analysis:
    """Collect bounded, located schema/collision diagnostics before constructing a map."""
    diagnostics: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    errors = 0

    def issue(code: str, path: str, message: str, related: str | None = None) -> None:
        nonlocal errors
        errors += 1
        if len(diagnostics) < MAX_DIAGNOSTICS:
            record = {"code": code, "path": path, "message": message}
            if related is not None:
                record["related_path"] = related
            diagnostics.append(record)

    def result(mapping: Dictionary | None = None) -> Analysis:
        return Analysis(mapping, rows, diagnostics, errors)

    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "mapping_version",
        "concepts",
    }:
        issue(
            "schema", "", "Expected schema_version, mapping_version and concepts only"
        )
        return result()
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        issue("schema", "/schema_version", "Expected integer version 1")
    version = document["mapping_version"]
    if not isinstance(version, str) or not version.strip() or len(version) > 128:
        issue(
            "version",
            "/mapping_version",
            "Use a nonempty version of at most 128 characters",
        )
    concepts = document["concepts"]
    if not isinstance(concepts, list) or len(concepts) > MAX_FORMS:
        issue("limit", "/concepts", "Expected a list of at most 10000 concepts")
        return result()
    ids: dict[str, str] = {}
    vocabularies: dict[str, dict[str, str]] = {"bam": {}, "boozle": {}}
    count = 0
    for index, concept in enumerate(concepts):
        path = f"/concepts/{index}"
        if (
            not isinstance(concept, dict)
            or not {"id", "forms"} <= set(concept)
            or set(concept) - {"id", "forms", "kind", "note"}
        ):
            issue(
                "schema", path, "Concept needs id/forms; optional fields are kind/note"
            )
            continue
        identifier = concept["id"]
        if not isinstance(identifier, str) or IDENTIFIER.fullmatch(identifier) is None:
            issue(
                "id",
                path + "/id",
                "Use a unique ASCII identifier of 1-64 characters starting with a letter",
            )
        elif identifier in ids:
            issue(
                "duplicate_id",
                path + "/id",
                "Concept id already exists",
                ids[identifier],
            )
        else:
            ids[identifier] = path + "/id"
        for field, limit in (("kind", 64), ("note", 2048)):
            if field in concept and (
                not isinstance(concept[field], str) or len(concept[field]) > limit
            ):
                issue(
                    "schema",
                    path + "/" + field,
                    f"Expected text of at most {limit} characters",
                )
        forms = concept["forms"]
        if not isinstance(forms, list) or not forms:
            issue(
                "schema",
                path + "/forms",
                "Provide at least one explicit bam/boozle form",
            )
            continue
        count += len(forms)
        if count > MAX_FORMS:
            issue("limit", path + "/forms", "Lexicon exceeds the 10000-form limit")
            return result()
        labels: dict[str, str] = {}
        for position, form in enumerate(forms):
            location = f"{path}/forms/{position}"
            if (
                not isinstance(form, dict)
                or not {"bam", "boozle"} <= set(form)
                or set(form) - {"bam", "boozle", "label"}
            ):
                issue(
                    "schema", location, "Form needs bam/boozle; optional field is label"
                )
                continue
            label = form.get("label")
            if "label" in form:
                if not isinstance(label, str) or not label.strip() or len(label) > 64:
                    issue(
                        "schema",
                        location + "/label",
                        "Use a nonempty form label of at most 64 characters",
                    )
                elif label in labels:
                    issue(
                        "duplicate_label",
                        location + "/label",
                        "Form label already exists in this concept",
                        labels[label],
                    )
                else:
                    labels[label] = location + "/label"
            valid = True
            for side in ("bam", "boozle"):
                term = form[side]
                term_path = location + "/" + side
                try:
                    validate_term(term)
                except ValueError as exc:
                    issue("term", term_path, str(exc))
                    valid = False
                    continue
                folded = term.casefold()
                if folded in vocabularies[side]:
                    issue(
                        "duplicate_" + side,
                        term_path,
                        "Every spelling needs its own unique reversible partner",
                        vocabularies[side][folded],
                    )
                else:
                    vocabularies[side][folded] = term_path
            if valid:
                rows.append(
                    {
                        "id": identifier,
                        "kind": concept.get("kind", ""),
                        "path": location,
                        **form,
                    }
                )
    for folded in sorted(vocabularies["bam"].keys() & vocabularies["boozle"].keys()):
        issue(
            "vocabulary_overlap",
            vocabularies["bam"][folded],
            "Source and neutral vocabularies must be disjoint",
            vocabularies["boozle"][folded],
        )
    if errors:
        return result()
    mapping = Dictionary({row["bam"]: row["boozle"] for row in rows}, version)
    # Exercise the actual matcher, including interactions between phrases and literal
    # targets. Newlines cannot occur inside terms, so each probe is independent.
    probe = "\n".join(term for row in rows for term in (row["bam"], row["boozle"]))
    try:
        Translator(mapping, max_output_chars=32 * 1024 * 1024).transform(probe)
    except ValueError as exc:
        issue("runtime_roundtrip", "/concepts", str(exc))
        return result()
    return result(mapping)


def _file(
    workspace: Workspace, name: str | Path, suffix: str, *, write: bool = False
) -> Path:
    path = workspace.resolve(name, write=write)
    if not path.name.casefold().endswith(suffix):
        raise ValueError(
            f"Use a {suffix} file; this suffix keeps domain metadata outside agent I/O"
        )
    return path


def _load(workspace: Workspace, name: str | Path) -> tuple[Path, bytes, Any, Analysis]:
    path = _file(workspace, name, ".lexicon.json")
    raw = workspace.read(path)
    document = parse_json(raw)
    return path, raw, document, analyze(document)


def export_lexicon(
    workspace: Workspace, mapping: Dictionary, output: str, version: str
) -> dict[str, Any]:
    """Export every real mapping as an editable concept, without guessing variants."""
    path = _file(workspace, output, ".lexicon.json", write=True)
    document = {
        "schema_version": 1,
        "mapping_version": version,
        "concepts": [
            {
                "id": "term_" + sha256(source.encode("utf-8"))[:24],
                "forms": [{"bam": source, "boozle": target, "label": "canonical"}],
            }
            for source, target in sorted(mapping.items())
        ],
    }
    analysis = analyze(document)
    if analysis.dictionary is None:
        return analysis.report()
    raw = json_bytes(document)
    if len(raw) > workspace.max_bytes:
        raise ValueError("Export exceeds the workspace file limit")
    atomic_write(path, raw)
    return {
        **analysis.report(),
        "status": "exported",
        "output": str(path.relative_to(workspace.root)),
        "source_dictionary_sha256": mapping.fingerprint,
        "lexicon_sha256": sha256(raw),
    }


def check_lexicon(workspace: Workspace, source: str) -> dict[str, Any]:
    _, raw, _, analysis = _load(workspace, source)
    return {**analysis.report(), "lexicon_sha256": sha256(raw)}


def query_lexicon(
    workspace: Workspace,
    source: str,
    *,
    query: str = "",
    side: str = "both",
    offset: int = 0,
    limit: int = 50,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Bounded literal substring query; pin the file digest between pages."""
    if (
        side not in {"bam", "boozle", "both"}
        or not isinstance(query, str)
        or len(query) > 512
    ):
        raise ValueError("Query is at most 512 characters; side is bam, boozle or both")
    if (
        type(offset) is not int
        or not 0 <= offset <= MAX_FORMS
        or type(limit) is not int
        or not 1 <= limit <= 200
    ):
        raise ValueError("Offset must be 0-10000 and limit 1-200")
    _, raw, _, analysis = _load(workspace, source)
    digest = sha256(raw)
    if expected_sha256 is not None and expected_sha256 != digest:
        raise ValueError("Lexicon changed between queries; start a fresh inspection")
    if analysis.dictionary is None:
        return {**analysis.report(), "lexicon_sha256": digest}
    sides = ("bam", "boozle") if side == "both" else (side,)
    matches = [
        row
        for row in analysis.rows
        if any(query.casefold() in row[key].casefold() for key in sides)
    ]
    end = offset + limit
    return {
        "ok": True,
        "status": "queried",
        "lexicon_sha256": digest,
        "total": len(matches),
        "offset": offset,
        "entries": matches[offset:end],
        "next_offset": end if end < len(matches) else None,
    }


def _changes(before: Dictionary, after: Dictionary) -> list[dict[str, Any]]:
    return [
        {
            "source": source,
            "before": before.get(source),
            "after": after.get(source),
            "change": "added"
            if source not in before
            else "removed"
            if source not in after
            else "changed",
        }
        for source in sorted(before.keys() | after.keys())
        if before.get(source) != after.get(source)
    ]


def _plan(
    workspace: Workspace,
    output: Path,
    inputs: dict[Path, str],
    result: bytes,
    record: dict[str, Any],
    details: dict[str, Any],
    recover_metadata: bool,
) -> ReviewedFilePlan:
    metadata = output.with_name("." + output.name + ".lexicon-receipt.json")
    if output in inputs or metadata in inputs:
        raise ValueError("Publish to a new output separate from all inputs")
    metadata_bytes = json_bytes(
        {
            "schema_version": 1,
            **record,
            "output_sha256": sha256(result),
            "inputs": {
                str(path.relative_to(workspace.root)): digest
                for path, digest in inputs.items()
            },
        }
    )
    if max(len(result), len(metadata_bytes)) > workspace.max_bytes:
        raise ValueError("Result or provenance exceeds the workspace file limit")
    plan = ReviewedFilePlan(
        workspace, output, metadata, inputs, result, metadata_bytes, details
    )
    plan.recheck(recover_metadata=recover_metadata)
    return plan


def prepare_compile(
    workspace: Workspace,
    source: str,
    output: str,
    *,
    base: str | None = None,
    offset: int = 0,
    limit: int = 100,
    expected_sha256: str | None = None,
    recover_metadata: bool = False,
) -> ReviewedFilePlan:
    """Compile the full candidate; pagination changes presentation, never reviewed bytes."""
    if (
        type(offset) is not int
        or not 0 <= offset <= 2 * MAX_FORMS
        or type(limit) is not int
        or not 1 <= limit <= 200
    ):
        raise ValueError("Change offset must be 0-20000 and limit 1-200")
    path, raw, document, analysis = _load(workspace, source)
    destination = _file(workspace, output, ".dictionary.json", write=True)
    inputs = {path: sha256(raw)}
    if expected_sha256 is not None and expected_sha256 != sha256(raw):
        raise ValueError("Lexicon changed after inspection")
    before = Dictionary({})
    base_path: Path | None = None
    snapshots = {str(path.relative_to(workspace.root)): raw.decode("utf-8")}
    if base is not None:
        base_path = workspace.resolve(base)
        baseline = workspace.read(base_path)
        before = Dictionary.from_document(parse_json(baseline))
        inputs[base_path] = sha256(baseline)
        snapshots[str(base_path.relative_to(workspace.root))] = baseline.decode("utf-8")
    mapping = analysis.dictionary
    if mapping is None:
        return ReviewedFilePlan(
            workspace,
            destination,
            destination.with_name("." + destination.name + ".lexicon-receipt.json"),
            inputs,
            None,
            None,
            analysis.report(),
        )
    if base is not None and mapping.version == before.version:
        raise ValueError(
            "Compilation requires a new mapping_version distinct from the base dictionary"
        )
    changes = _changes(before, mapping)
    end = offset + limit
    details = {
        **analysis.report(),
        "operation": "compile_lexicon",
        "base_dictionary_sha256": before.fingerprint if base else None,
        "changes": changes[offset:end],
        "change_count": len(changes),
        "change_counts": {
            kind: sum(item["change"] == kind for item in changes)
            for kind in ("added", "changed", "removed")
        },
        "changes_sha256": sha256(json_bytes(changes)),
        "offset": offset,
        "next_offset": end if end < len(changes) else None,
        "changes_complete": offset == 0 and end >= len(changes),
    }
    return _plan(
        workspace,
        destination,
        inputs,
        json_bytes(mapping.document()),
        {
            "kind": KIND,
            "lexicon": document,
            "base": before.document() if base else None,
            "changes": changes,
            "source": str(path.relative_to(workspace.root)),
            "baseline": str(base_path.relative_to(workspace.root))
            if base_path is not None
            else None,
            "snapshots": snapshots,
        },
        details,
        recover_metadata,
    )


def prepare_edit(
    workspace: Workspace,
    source: str,
    patch: str,
    output: str,
    *,
    recover_metadata: bool = False,
) -> ReviewedFilePlan:
    """Apply explicit concept upserts/removals into a reviewed new authoring file."""
    path, raw, document, analysis = _load(workspace, source)
    if analysis.dictionary is None:
        raise ValueError(
            "Source lexicon is invalid; inspect lexicon-check diagnostics first"
        )
    patch_path = _file(workspace, patch, ".lexicon-patch.json")
    patch_raw = workspace.read(patch_path)
    edit = parse_json(patch_raw)
    if (
        not isinstance(edit, dict)
        or set(edit) != {"schema_version", "mapping_version", "upsert", "remove"}
        or type(edit["schema_version"]) is not int
        or edit["schema_version"] != 1
    ):
        raise ValueError(
            "Patch needs schema_version 1, mapping_version, upsert and remove"
        )
    additions, removals = edit["upsert"], edit["remove"]
    if (
        not isinstance(additions, list)
        or not isinstance(removals, list)
        or len(additions) + len(removals) > MAX_FORMS
    ):
        raise ValueError(
            "Patch upsert/remove must be lists with at most 10000 operations"
        )
    concepts = {concept["id"]: concept for concept in document["concepts"]}
    seen: set[str] = set()
    for identifier in removals:
        if (
            not isinstance(identifier, str)
            or identifier not in concepts
            or identifier in seen
        ):
            raise ValueError("Remove must name each existing concept id once")
        seen.add(identifier)
        del concepts[identifier]
    for concept in additions:
        identifier = concept.get("id") if isinstance(concept, dict) else None
        if (
            not isinstance(identifier, str)
            or IDENTIFIER.fullmatch(identifier) is None
            or identifier in seen
        ):
            raise ValueError("Upsert needs a unique concept id, disjoint from removals")
        seen.add(identifier)
        concepts[identifier] = concept
    candidate = {
        "schema_version": 1,
        "mapping_version": edit["mapping_version"],
        "concepts": list(concepts.values()),
    }
    checked = analyze(candidate)
    destination = _file(workspace, output, ".lexicon.json", write=True)
    inputs = {path: sha256(raw), patch_path: sha256(patch_raw)}
    if checked.dictionary is None:
        return ReviewedFilePlan(
            workspace,
            destination,
            destination.with_name("." + destination.name + ".lexicon-receipt.json"),
            inputs,
            None,
            None,
            checked.report(),
        )
    changes = _changes(analysis.dictionary, checked.dictionary)
    # The patch is already bounded and explicit. The receipt retains full concept
    # metadata changes even when the flattened dictionary happens to be unchanged.
    return _plan(
        workspace,
        destination,
        inputs,
        json_bytes(candidate),
        {"kind": "bamboozle.lexicon-edit", "source": document, "patch": edit},
        {
            **checked.report(),
            "operation": "edit_lexicon",
            "concepts_upserted": len(additions),
            "concepts_removed": len(removals),
            "change_count": len(changes),
            "changes_sha256": sha256(json_bytes(changes)),
            "patch_sha256": sha256(patch_raw),
            **_diff(raw, json_bytes(candidate)),
        },
        recover_metadata,
    )


def verify_compilation(workspace: Workspace, source: str) -> dict[str, Any]:
    """Check a compiled artifact against retained authoring/base snapshots and lineage.

    Hashes establish local consistency, not the identity of a trusted signer.
    Current input drift is reported separately from the reproducible saved artifact.
    """
    path = _file(workspace, source, ".dictionary.json")
    raw = workspace.read(path)
    record = parse_json(
        workspace.read(path.with_name("." + path.name + ".lexicon-receipt.json"))
    )
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "schema_version",
            "kind",
            "lexicon",
            "base",
            "changes",
            "output_sha256",
            "inputs",
            "source",
            "baseline",
            "snapshots",
        }
        or type(record["schema_version"]) is not int
        or record["schema_version"] != 1
        or record["kind"] != KIND
    ):
        raise ValueError("Invalid lexicon compilation receipt")
    analysis = analyze(record["lexicon"])
    mapping = analysis.dictionary
    before = (
        Dictionary({})
        if record["base"] is None
        else Dictionary.from_document(record["base"])
    )
    if (
        mapping is None
        or raw != json_bytes(mapping.document())
        or sha256(raw) != record["output_sha256"]
        or record["changes"] != _changes(before, mapping)
    ):
        raise ValueError(
            "Compiled dictionary or provenance does not reproduce the retained lexicon"
        )
    if not isinstance(record["inputs"], dict) or not 1 <= len(record["inputs"]) <= 2:
        raise ValueError("Invalid compilation input lineage")
    snapshots = record["snapshots"]
    if (
        not isinstance(snapshots, dict)
        or set(snapshots) != set(record["inputs"])
        or any(not isinstance(value, str) for value in snapshots.values())
    ):
        raise ValueError("Invalid compilation snapshots")
    source_name, baseline_name = record["source"], record["baseline"]
    if (
        not isinstance(source_name, str)
        or source_name not in snapshots
        or (
            baseline_name is not None
            and (
                not isinstance(baseline_name, str)
                or baseline_name not in snapshots
                or source_name == baseline_name
            )
        )
    ):
        raise ValueError("Invalid compilation snapshot paths")
    if set(snapshots) != (
        {source_name} if baseline_name is None else {source_name, baseline_name}
    ):
        raise ValueError("Unexpected compilation snapshot")
    if (
        parse_json(snapshots[source_name].encode("utf-8")) != record["lexicon"]
        or (baseline_name is None and record["base"] is not None)
        or (
            baseline_name is not None
            and Dictionary.from_document(
                parse_json(snapshots[baseline_name].encode("utf-8"))
            ).document()
            != record["base"]
        )
    ):
        raise ValueError("Compilation snapshots disagree with provenance")
    current = []
    for name, digest in record["inputs"].items():
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Invalid compilation input digest")
        if (
            not isinstance(snapshots[name], str)
            or sha256(snapshots[name].encode("utf-8")) != digest
        ):
            raise ValueError("Compilation snapshot hash does not match input lineage")
        input_path = workspace.resolve(name)
        state = (
            "missing"
            if not input_path.exists()
            else "unchanged"
            if sha256(workspace.read(input_path)) == digest
            else "changed"
        )
        current.append({"path": name, "state": state})
    return {
        "ok": True,
        "status": "verified",
        "dictionary_sha256": mapping.fingerprint,
        "mapping_version": mapping.version,
        "forms": len(mapping),
        "inputs": current,
        "current_inputs_unchanged": all(
            item["state"] == "unchanged" for item in current
        ),
    }
