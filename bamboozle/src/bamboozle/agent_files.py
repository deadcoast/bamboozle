"""Cipher-mediated agent file access with private originals and stale-write checks."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from bamboozle.cipher import CipherEngine
from bamboozle.dictionary import Dictionary
from bamboozle.service import OperationError, ledger_path
from bamboozle.utils import PublicationError, atomic_write, json_bytes, sha256, utc_now
from bamboozle.workspace import Workspace

HISTORY = Path(".vigiles/cipher/changes")
MAX_READS = 256


def source_path(
    workspace: Workspace, name: str, *, private_paths: set[Path], write: bool = False
) -> Path:
    """Shared private-source boundary for agent I/O and project views."""
    path = workspace.resolve(name, write=write)
    relative = path.relative_to(workspace.root)
    if (
        any(
            part.casefold() in {".vigiles", ".mivim", ".local"}
            for part in relative.parts
        )
        or path in private_paths
        or path.name.casefold() in {"cipher_dict.json", "profanity_dict.json"}
        or path.name.casefold().endswith(
            (
                ".key",
                ".key.bak",
                ".scm_ledger.json",
                ".vigiles-restore.json",
                ".lexicon.json",
                ".lexicon-patch.json",
                ".dictionary.json",
                ".lexicon-receipt.json",
                ".fixture-rules.json",
            )
        )
        or ".key." in path.name.casefold()
    ):
        raise ValueError(
            "Private cipher metadata and keys are outside agent file access"
        )
    return path


class AgentFiles:
    """Read through the encoder and write through the decoder, never returning raw source.

    Existing-file writes require the hash of a previous read, either retained by
    this server or supplied explicitly after a reconnect. All writes retain a
    private intent record and the pre-edit bytes before publishing the decoded text.
    """

    def __init__(
        self,
        workspace: Workspace,
        dictionary: Dictionary,
        *,
        private_paths: tuple[Path, ...] = (),
    ):
        self.workspace = workspace
        self.dictionary = dictionary
        self.cipher = CipherEngine(dictionary, max_output_chars=workspace.max_bytes)
        self.private_paths = {workspace.resolve(path) for path in private_paths}
        self.reads: OrderedDict[Path, str] = OrderedDict()
        self.lock = threading.RLock()

    def _path(self, name: str, *, write: bool = False) -> Path:
        return source_path(
            self.workspace, name, private_paths=self.private_paths, write=write
        )

    def _remember(self, path: Path, digest: str) -> None:
        self.reads[path] = digest
        self.reads.move_to_end(path)
        while len(self.reads) > MAX_READS:
            self.reads.popitem(last=False)

    def read(self, file_path: str) -> dict[str, Any]:
        with self.lock:
            path = self._path(file_path)
            raw = self.workspace.read(path)
            encoded = self.cipher.encode(raw.decode("utf-8"))
            if len(encoded.encode("utf-8")) > self.workspace.max_bytes:
                raise ValueError("Encoded agent content exceeds the file limit")
            digest = sha256(raw)
            self._remember(path, digest)
            return {
                "status": "read",
                "file_path": path.relative_to(self.workspace.root).as_posix(),
                "content": encoded,
                "source_sha256": digest,
                "dictionary_sha256": self.dictionary.fingerprint,
                "marker_version": self.cipher.marker_version,
                "terminology_contract": "Edit or generate code in this vocabulary. Preserve __SCM_LIT_...__, __SCM_CASE_...__, and __SCM_CASE_x_<hex>__ prefix escapes exactly. For newly authored literal marker syntax use bamboozle.markers.quote_prefixes on the raw snippet only; its payload contains only a reserved prefix. Submit neutral content to write_safe_file; the cipher restores domain terms locally.",
            }

    def write(
        self,
        file_path: str,
        content: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        if (
            not isinstance(content, str)
            or len(content.encode("utf-8")) > self.workspace.max_bytes
        ):
            raise ValueError("Agent content must be UTF-8 text within the file limit")
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_sha256)
        ):
            raise ValueError(
                "expected_sha256 must be the source hash returned by read_safe_file"
            )
        decoded = self.cipher.decode(content).encode("utf-8")
        if len(decoded) > self.workspace.max_bytes:
            raise ValueError("Decoded agent content exceeds the file limit")
        with self.lock:
            path = self._path(file_path, write=True)
            if (
                ledger_path(path).exists()
                or path.with_name(f".{path.name}.vigiles-restore.json").exists()
            ):
                raise ValueError(
                    "Destination has translation metadata; choose a source file or new output"
                )
            before = self.workspace.read(path) if path.exists() else None
            expected = (
                expected_sha256 if expected_sha256 is not None else self.reads.get(path)
            )
            if before is not None:
                if expected is None:
                    raise ValueError(
                        "Read the existing file with read_safe_file before editing it"
                    )
                if sha256(before) != expected:
                    raise ValueError(
                        "Source changed since the agent read; read it again before writing"
                    )
            elif expected is not None:
                raise ValueError(
                    "Previously read source is missing; refusing to recreate it silently"
                )
            operation_id = uuid.uuid4().hex
            directory = self.workspace.resolve(
                HISTORY / operation_id / "intent.json", write=True
            ).parent
            directory.mkdir(parents=True)
            report: dict[str, Any] = {
                "schema_version": 1,
                "operation_id": operation_id,
                "file_path": path.relative_to(self.workspace.root).as_posix(),
                "dictionary_sha256": self.dictionary.fingerprint,
                "marker_version": self.cipher.marker_version,
                "before_sha256": sha256(before) if before is not None else None,
                "output_sha256": sha256(decoded),
                "output_bytes": len(decoded),
                "backup_saved": False,
                "output_saved": False,
                "receipt_saved": False,
                "history": directory.relative_to(self.workspace.root).as_posix(),
            }
            try:
                if before is not None:
                    try:
                        atomic_write(
                            self.workspace.resolve(
                                directory / "original.bin", write=True
                            ),
                            before,
                        )
                    except PublicationError:
                        report["backup_saved"] = True
                        raise
                    report["backup_saved"] = True
                atomic_write(
                    self.workspace.resolve(directory / "decoded.bin", write=True),
                    decoded,
                )
                atomic_write(
                    self.workspace.resolve(directory / "intent.json", write=True),
                    json_bytes(
                        {
                            **report,
                            "kind": "vigiles.cipher-write",
                            "created": utc_now(),
                        }
                    ),
                )
                path = self._path(file_path, write=True)
                current = self.workspace.read(path) if path.exists() else None
                if current != before:
                    raise ValueError(
                        "Source changed before publication; retained history is available"
                    )
                try:
                    atomic_write(path, decoded, overwrite=before is not None)
                except PublicationError:
                    report["output_saved"] = True
                    raise
                report["output_saved"] = True
                if self.workspace.read(path) != decoded:
                    raise ValueError(
                        "Source changed after publication; inspect the retained history"
                    )
                self._remember(path, sha256(decoded))
                result = {**report, "status": "saved", "receipt_saved": True}
                try:
                    atomic_write(
                        self.workspace.resolve(directory / "result.json", write=True),
                        json_bytes(result),
                    )
                except PublicationError:
                    report["receipt_saved"] = True
                    raise
                return result
            except (OSError, ValueError) as exc:
                report.update(status="error", error=str(exc))
                raise OperationError(str(exc), report) from exc
