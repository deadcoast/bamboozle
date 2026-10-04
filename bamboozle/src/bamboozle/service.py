"""Preview, publication, and per-file integrity contracts."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bamboozle.dictionary import Dictionary
from bamboozle.engine import Mode, Translator
from bamboozle.formats import DocumentCodec, FormatRules
from bamboozle.markers import CURRENT, LEGACY, validate_version
from bamboozle.structure import Guard, prepare_guard
from bamboozle.utils import (
    MAX_FILE_BYTES,
    PublicationError,
    atomic_write,
    json_bytes,
    parse_json,
    sha256,
    utc_now,
)
from bamboozle.workspace import Workspace

LEDGER_KIND = "vigiles.translation"


class OperationError(OSError):
    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def scan(
    workspace: Workspace,
    source: str | Path,
    dictionary: Dictionary,
    *,
    max_matches: int = 1000,
) -> dict[str, Any]:
    """Read-only operator inspection using the same matches as the encoder."""
    path = workspace.resolve(source)
    raw = workspace.read(path)
    result = Translator(dictionary, max_output_chars=workspace.max_bytes).scan(
        raw.decode("utf-8"), max_matches=max_matches
    )
    return {
        "schema_version": 1,
        "status": "scanned",
        "input": path.relative_to(workspace.root).as_posix(),
        "input_sha256": sha256(raw),
        "input_bytes": len(raw),
        "output_saved": False,
        **result,
    }


def ledger_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.scm_ledger.json")


def _ledger(
    original: bytes,
    encoded: bytes,
    dictionary: Dictionary,
    limit: int = MAX_FILE_BYTES,
    *,
    rules: FormatRules | None = None,
    context: dict[str, Any] | None = None,
    marker_version: int = CURRENT,
) -> bytes:
    validate_version(marker_version)
    rules = rules or FormatRules()
    structured = rules.format != "text" or bool(rules.protect)
    metadata = json_bytes(
        {
            "kind": LEDGER_KIND,
            "schema_version": 3
            if marker_version == CURRENT
            else 2
            if structured
            else 1,
            **({"marker_version": marker_version} if marker_version == CURRENT else {}),
            **(
                {"document": {"rules": rules.document(), "context": context or {}}}
                if (structured or marker_version == CURRENT) and rules is not None
                else {}
            ),
            "created": utc_now(),
            "original_sha256": sha256(original),
            "encoded_sha256": sha256(encoded),
            "dictionary_sha256": dictionary.fingerprint,
            "dictionary": dictionary.document(),
        }
    )
    if len(metadata) > limit:
        raise ValueError("Translation ledger exceeds the workspace file limit")
    return metadata


def _ledger_dictionary(ledger: bytes) -> tuple[Dictionary, dict[str, Any]]:
    """Read a saved mapping without requiring the agent's output to be unchanged."""
    data = parse_json(ledger)
    if (
        not isinstance(data, dict)
        or data.get("kind") != LEDGER_KIND
        or type(data.get("schema_version")) is not int
        or data["schema_version"] not in (1, 2, 3)
    ):
        raise ValueError("Unsupported translation ledger")
    dictionary = Dictionary.from_document(data.get("dictionary"))
    if dictionary.fingerprint != data.get("dictionary_sha256"):
        raise ValueError("Ledger dictionary digest does not match")
    rules, context = ledger_format(data)
    DocumentCodec(
        dictionary, rules, marker_version=ledger_marker_version(data)
    ).validate_context(context)
    for name in ("original_sha256", "encoded_sha256"):
        value = data.get(name)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise ValueError(f"Invalid ledger digest: {name}")
    return dictionary, data


def ledger_marker_version(data: dict[str, Any]) -> int:
    if type(data.get("schema_version")) is not int or data["schema_version"] not in (
        1,
        2,
        3,
    ):
        raise ValueError("Unsupported translation ledger")
    if data["schema_version"] < 3:
        if "marker_version" in data:
            raise ValueError("Legacy ledgers cannot select a marker version")
        return LEGACY
    if validate_version(data.get("marker_version")) != CURRENT:
        raise ValueError("Version-3 ledgers require marker version 2")
    return CURRENT


def ledger_format(data: dict[str, Any]) -> tuple[FormatRules, dict[str, Any]]:
    ledger_marker_version(data)
    if data["schema_version"] == 1:
        if "document" in data:
            raise ValueError("Version-1 ledgers cannot carry document rules")
        return FormatRules(), {}
    document = data.get("document")
    if not isinstance(document, dict) or set(document) != {"rules", "context"}:
        raise ValueError("Version-2/3 ledger requires document rules and context")
    return FormatRules.from_document(document["rules"]), document["context"]


def _restore(encoded: bytes, ledger: bytes, limit: int = MAX_FILE_BYTES) -> bytes:
    dictionary, data = _ledger_dictionary(ledger)
    if sha256(encoded) != data.get("encoded_sha256"):
        raise ValueError("Encoded file changed; strict restoration refused")
    rules, context = ledger_format(data)
    result = (
        DocumentCodec(
            dictionary, rules, limit=limit, marker_version=ledger_marker_version(data)
        )
        .transform(encoded.decode("utf-8"), "reverse", context=context)
        .text.encode("utf-8")
    )
    if len(result) > limit:
        raise ValueError("Restored output exceeds the workspace file limit")
    if sha256(result) != data.get("original_sha256"):
        raise ValueError("Restored text does not match the original digest")
    return result


@dataclass(frozen=True)
class ConversionPlan:
    workspace: Workspace
    source: Path
    output: Path
    mode: Mode
    source_digest: str
    result: bytes = field(repr=False)
    metadata: bytes | None = field(default=None, repr=False)
    source_ledger: bytes | None = field(default=None, repr=False)
    substitutions: int = 0
    literals: int = 0
    source_ledger_path: Path | None = None
    dictionary_inputs: dict[Path, bytes] = field(default_factory=dict, repr=False)
    dictionary_sha256: str = ""
    mapping_source: str = ""
    strict: bool = False
    format_rules: FormatRules = field(default_factory=FormatRules)
    marker_version: int = CURRENT
    escapes: int = 0
    structure: Guard = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "structure",
            prepare_guard(
                self.workspace,
                {self.output.relative_to(self.workspace.root).as_posix(): self.result},
            ),
        )

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "marker_version": self.marker_version,
            "escaped_prefixes": self.escapes,
            "status": "preview",
            "mode": self.mode,
            "input": str(self.source.relative_to(self.workspace.root)),
            "output": str(self.output.relative_to(self.workspace.root)),
            "input_sha256": self.source_digest,
            "output_sha256": sha256(self.result),
            "output_bytes": len(self.result),
            "substitutions": self.substitutions,
            "literals": self.literals,
            "dictionary_sha256": self.dictionary_sha256,
            "mapping_source": self.mapping_source,
            "strict": self.strict,
            "document_rules": self.format_rules.document(),
            "ledger_input": (
                str(self.source_ledger_path.relative_to(self.workspace.root))
                if self.source_ledger is not None
                and self.source_ledger_path is not None
                else None
            ),
            "output_saved": False,
            "ledger_saved": False,
            **(
                {
                    "structure": self.structure.certificate,
                    "structural_validation": self.structure.result,
                    "ok": self.structure.result is None or self.structure.result["ok"],
                }
                if self.structure.certificate
                else {}
            ),
        }

    def _recheck(self) -> None:
        self.workspace.resolve(self.output, write=True)
        if self.output.exists() or ledger_path(self.output).exists():
            raise FileExistsError(
                f"Destination or its ledger already exists: {self.output}"
            )
        if sha256(self.workspace.read(self.source)) != self.source_digest:
            raise ValueError("Input changed after preview; create a new preview")
        if self.source_ledger_path is not None:
            ledger = self.workspace.resolve(self.source_ledger_path)
            if self.source_ledger is None:
                if ledger.exists():
                    raise ValueError(
                        "Ledger appeared after preview; create a new preview"
                    )
            elif self.workspace.read(ledger) != self.source_ledger:
                raise ValueError("Ledger changed after preview; create a new preview")
        for path, content in self.dictionary_inputs.items():
            if self.workspace.read(path) != content:
                raise ValueError(
                    "Dictionary changed after preview; create a new preview"
                )

    def apply(self) -> dict[str, Any]:
        report = self.report()
        self._recheck()
        try:
            self.structure.recheck()
            try:
                atomic_write(self.output, self.result)
            except PublicationError:
                report["output_saved"] = True
                raise
            report["output_saved"] = True
            if self.metadata is not None:
                destination = self.workspace.resolve(
                    ledger_path(self.output), write=True
                )
                try:
                    atomic_write(destination, self.metadata)
                except PublicationError:
                    report["ledger_saved"] = True
                    raise
                report["ledger_saved"] = True
            self.structure.recheck()
        except (OSError, ValueError) as exc:
            report.update(status="error", error=str(exc))
            raise OperationError(str(exc), report) from exc
        report["status"] = "saved"
        return report


def prepare(
    workspace: Workspace,
    source: str | Path,
    *,
    mode: Mode = "forward",
    output: str | Path | None = None,
    dictionary: Dictionary | str | Path | None = None,
    ledger: str | Path | None = None,
    strict: bool = False,
    format_rules: FormatRules | None = None,
) -> ConversionPlan:
    if mode not in ("forward", "reverse"):
        raise ValueError("Unknown conversion mode")
    if mode == "forward" and (strict or ledger is not None):
        raise ValueError(
            "Strict checking and a saved ledger apply only to reverse translation"
        )
    path = workspace.resolve(source)
    if path.suffix.casefold() == ".key":
        raise ValueError("Key files cannot be passed through text translation")
    original = workspace.read(path)
    suffix = "_safe" if mode == "forward" else "_ready"
    destination = workspace.resolve(
        output if output is not None else path.with_stem(path.stem + suffix), write=True
    )
    if destination == path:
        raise ValueError("Choose a separate destination; source files are preserved")
    if destination.exists() or ledger_path(destination).exists():
        raise FileExistsError(
            f"Destination or its ledger already exists: {destination}"
        )
    mapping_inputs = {}
    supplied = dictionary
    if isinstance(dictionary, (str, Path)):
        dictionary_file = workspace.resolve(dictionary)
        dictionary_bytes = workspace.read(dictionary_file)
        supplied = Dictionary.from_document(parse_json(dictionary_bytes))
        mapping_inputs[dictionary_file] = dictionary_bytes
    if supplied is not None and not isinstance(supplied, Dictionary):
        raise TypeError(
            "Dictionary must be a validated Dictionary or a workspace file path"
        )
    mapping_source = (
        "dictionary_file"
        if mapping_inputs
        else "supplied_dictionary"
        if supplied is not None
        else "packaged_dictionary"
    )
    if mode == "forward":
        if ledger_path(path).exists():
            raise ValueError("Input already has a translation ledger")
        mapping = supplied if isinstance(supplied, Dictionary) else Dictionary.load()
        rules = format_rules or FormatRules()
        converted = DocumentCodec(mapping, rules, limit=workspace.max_bytes).transform(
            original.decode("utf-8")
        )
        result = converted.text.encode("utf-8")
        if len(result) > workspace.max_bytes:
            raise ValueError("Translated output exceeds the workspace file limit")
        return ConversionPlan(
            workspace,
            path,
            destination,
            mode,
            sha256(original),
            result,
            _ledger(
                original,
                result,
                mapping,
                workspace.max_bytes,
                rules=rules,
                context=converted.context,
            ),
            None,
            converted.substitutions,
            converted.literals,
            dictionary_inputs=mapping_inputs,
            dictionary_sha256=mapping.fingerprint,
            mapping_source=mapping_source,
            format_rules=rules,
            escapes=converted.escapes,
        )
    saved_path = workspace.resolve(ledger if ledger is not None else ledger_path(path))
    metadata = None
    context: dict[str, Any] = {}
    rules = format_rules or FormatRules()
    marker_version = CURRENT
    if ledger is not None or saved_path.exists():
        metadata = workspace.read(saved_path)
        mapping, record = _ledger_dictionary(metadata)
        rules, context = ledger_format(record)
        marker_version = ledger_marker_version(record)
        if format_rules is not None and rules != format_rules:
            raise ValueError("Explicit format rules do not match the saved ledger")
        if (
            isinstance(supplied, Dictionary)
            and supplied.fingerprint != mapping.fingerprint
        ):
            raise ValueError(
                "Explicit dictionary does not match the saved ledger dictionary"
            )
        mapping_source = "saved_ledger"
    else:
        if strict:
            raise ValueError("Strict restoration requires a saved translation ledger")
        mapping = supplied if isinstance(supplied, Dictionary) else Dictionary.load()
    converted = DocumentCodec(
        mapping, rules, limit=workspace.max_bytes, marker_version=marker_version
    ).transform(
        original.decode("utf-8"),
        "reverse",
        context=context,
    )
    result = converted.text.encode("utf-8")
    if len(result) > workspace.max_bytes:
        raise ValueError("Translated output exceeds the workspace file limit")
    if strict:
        assert metadata is not None
        result = _restore(original, metadata, workspace.max_bytes)
    return ConversionPlan(
        workspace,
        path,
        destination,
        mode,
        sha256(original),
        result,
        source_ledger=metadata,
        substitutions=converted.substitutions,
        literals=converted.literals,
        source_ledger_path=saved_path,
        dictionary_inputs=mapping_inputs,
        dictionary_sha256=mapping.fingerprint,
        mapping_source=mapping_source,
        strict=strict,
        format_rules=rules,
        marker_version=marker_version,
        escapes=converted.escapes,
    )


def preview(plan: ConversionPlan) -> dict[str, Any]:
    plan._recheck()
    report = plan.report()
    source = plan.workspace.read(plan.source)
    if sha256(source) != plan.source_digest:
        raise ValueError("Input changed during preview")
    before = source.decode("utf-8")
    report["diff"] = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            plan.result.decode("utf-8").splitlines(keepends=True),
            fromfile=report["input"],
            tofile=report["output"],
        )
    )
    return report


def verify(workspace: Workspace, source: str | Path) -> dict[str, Any]:
    path = workspace.resolve(source)
    encoded = workspace.read(path)
    result = _restore(encoded, workspace.read(ledger_path(path)), workspace.max_bytes)
    return {
        "schema_version": 1,
        "status": "verified",
        "input": str(path.relative_to(workspace.root)),
        "encoded_sha256": sha256(encoded),
        "original_sha256": sha256(result),
    }


def recover(
    workspace: Workspace,
    source: str | Path,
    original: str | Path,
    dictionary: Dictionary | None = None,
    *,
    apply: bool = False,
    format_rules: FormatRules | None = None,
) -> dict[str, Any]:
    path, original_path = workspace.resolve(source), workspace.resolve(original)
    if path == original_path:
        raise ValueError("Recovery requires a separate original file")
    destination = workspace.resolve(ledger_path(path), write=True)
    if destination.exists():
        raise FileExistsError(f"Ledger already exists: {destination}")
    encoded, raw = workspace.read(path), workspace.read(original_path)
    mapping = dictionary if dictionary is not None else Dictionary.load()
    rules = format_rules or FormatRules()
    converted = DocumentCodec(mapping, rules, limit=workspace.max_bytes).transform(
        raw.decode("utf-8")
    )
    if converted.text.encode("utf-8") != encoded:
        raise ValueError(
            "Original and encoded files do not form an exact translation pair"
        )
    metadata = _ledger(
        raw,
        encoded,
        mapping,
        workspace.max_bytes,
        rules=rules,
        context=converted.context,
    )
    report = {
        "schema_version": 1,
        "status": "recovery_preview",
        "ledger_saved": False,
        "input": str(path.relative_to(workspace.root)),
    }
    if apply:
        if workspace.read(path) != encoded or workspace.read(original_path) != raw:
            raise ValueError("Recovery inputs changed")
        try:
            atomic_write(destination, metadata)
        except PublicationError as exc:
            report.update(status="error", ledger_saved=True)
            raise OperationError(str(exc), report) from exc
        report.update(status="recovered", ledger_saved=True)
    return report
