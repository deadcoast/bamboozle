"""Preview, publication, and per-file integrity contracts."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bamboozle.dictionary import Dictionary
from bamboozle.engine import Mode, Translator
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


def ledger_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.scm_ledger.json")


def _ledger(
    original: bytes, encoded: bytes, dictionary: Dictionary, limit: int = MAX_FILE_BYTES
) -> bytes:
    metadata = json_bytes(
        {
            "kind": LEDGER_KIND,
            "schema_version": 1,
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


def _restore(encoded: bytes, ledger: bytes, limit: int = MAX_FILE_BYTES) -> bytes:
    data = parse_json(ledger)
    if (
        not isinstance(data, dict)
        or data.get("kind") != LEDGER_KIND
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("Unsupported translation ledger")
    dictionary = Dictionary.from_document(data.get("dictionary"))
    if dictionary.fingerprint != data.get("dictionary_sha256"):
        raise ValueError("Ledger dictionary digest does not match")
    if sha256(encoded) != data.get("encoded_sha256"):
        raise ValueError("Encoded file changed; strict restoration refused")
    result = (
        Translator(dictionary, max_output_chars=limit)
        .reverse(encoded.decode("utf-8"))
        .encode("utf-8")
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

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": "preview",
            "mode": self.mode,
            "input": str(self.source.relative_to(self.workspace.root)),
            "output": str(self.output.relative_to(self.workspace.root)),
            "input_sha256": self.source_digest,
            "output_sha256": sha256(self.result),
            "output_bytes": len(self.result),
            "substitutions": self.substitutions,
            "literals": self.literals,
            "output_saved": False,
            "ledger_saved": False,
        }

    def _recheck(self) -> None:
        self.workspace.resolve(self.output, write=True)
        if self.output.exists() or ledger_path(self.output).exists():
            raise FileExistsError(
                f"Destination or its ledger already exists: {self.output}"
            )
        if sha256(self.workspace.read(self.source)) != self.source_digest:
            raise ValueError("Input changed after preview; create a new preview")
        if (
            self.source_ledger is not None
            and self.workspace.read(ledger_path(self.source)) != self.source_ledger
        ):
            raise ValueError("Ledger changed after preview; create a new preview")

    def apply(self) -> dict[str, Any]:
        report = self.report()
        self._recheck()
        try:
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
    dictionary: Dictionary | None = None,
) -> ConversionPlan:
    if mode not in ("forward", "reverse"):
        raise ValueError("Unknown conversion mode")
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
    if mode == "forward":
        if ledger_path(path).exists():
            raise ValueError("Input already has a translation ledger")
        mapping = dictionary if dictionary is not None else Dictionary.load()
        converted = Translator(mapping, max_output_chars=workspace.max_bytes).transform(
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
            _ledger(original, result, mapping, workspace.max_bytes),
            None,
            converted.substitutions,
            converted.literals,
        )
    metadata = workspace.read(ledger_path(path))
    result = _restore(original, metadata, workspace.max_bytes)
    return ConversionPlan(
        workspace,
        path,
        destination,
        mode,
        sha256(original),
        result,
        source_ledger=metadata,
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
) -> dict[str, Any]:
    path, original_path = workspace.resolve(source), workspace.resolve(original)
    if path == original_path:
        raise ValueError("Recovery requires a separate original file")
    destination = workspace.resolve(ledger_path(path), write=True)
    if destination.exists():
        raise FileExistsError(f"Ledger already exists: {destination}")
    encoded, raw = workspace.read(path), workspace.read(original_path)
    mapping = dictionary if dictionary is not None else Dictionary.load()
    if (
        Translator(mapping, max_output_chars=workspace.max_bytes)
        .forward(raw.decode("utf-8"))
        .encode("utf-8")
        != encoded
    ):
        raise ValueError(
            "Original and encoded files do not form an exact translation pair"
        )
    metadata = _ledger(raw, encoded, mapping, workspace.max_bytes)
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
