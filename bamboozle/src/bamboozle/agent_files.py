"""Cipher-mediated agent file access with private originals and stale-write checks."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bamboozle.cipher import CipherEngine
from bamboozle.dictionary import Dictionary
from bamboozle.formats import DocumentCodec, FormatRules
from bamboozle.service import OperationError, ledger_path
from bamboozle.utils import PublicationError, atomic_write, json_bytes, sha256, utc_now
from bamboozle.workspace import Workspace

if TYPE_CHECKING:
    from bamboozle.agent_policy import AgentPolicy

HISTORY = Path(".vigiles/cipher/changes")
MAX_READS = 256


def check_public_name(path: Path) -> None:
    """Reject reserved private names without depending on the process directory."""
    if (
        any(part.casefold() in {".vigiles", ".mivim", ".local"} for part in path.parts)
        or path.name.casefold() in {"cipher_dict.json", "profanity_dict.json"}
        or path.name.casefold().startswith(".env")
        or path.name.casefold().endswith(
            (
                ".key",
                ".key.bak",
                ".pem",
                ".p12",
                ".pfx",
                ".scm_ledger.json",
                ".vigiles-restore.json",
                ".lexicon.json",
                ".lexicon-patch.json",
                ".dictionary.json",
                ".lexicon-receipt.json",
                ".fixture-rules.json",
                ".cipher-recipe.json",
                ".cipher-receipt.json",
                ".agent-policy.json",
                ".agent-checks.json",
            )
        )
        or ".key." in path.name.casefold()
    ):
        raise ValueError(
            "Private cipher metadata and keys are outside agent file access"
        )


def source_path(
    workspace: Workspace, name: str, *, private_paths: set[Path], write: bool = False
) -> Path:
    """Shared private-source boundary for agent I/O and project views."""
    path = workspace.resolve(name, write=write)
    check_public_name(path.relative_to(workspace.root))
    if path in private_paths:
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
        policy: AgentPolicy | None = None,
    ):
        self.workspace = workspace
        self.dictionary = dictionary
        self.cipher = CipherEngine(dictionary, max_output_chars=workspace.max_bytes)
        self.private_paths = {workspace.resolve(path) for path in private_paths}
        self.policy = policy
        if policy is not None:
            if policy.workspace.root != workspace.root:
                raise ValueError("Source-agent policy belongs to another workspace")
            self.private_paths.add(policy.path)
        self.reads: OrderedDict[Path, tuple[str, str]] = OrderedDict()
        self.lock = threading.RLock()

    def _path(self, name: str, *, write: bool = False) -> Path:
        return source_path(
            self.workspace, name, private_paths=self.private_paths, write=write
        )

    def _remember(self, path: Path, digest: str, context: str) -> None:
        self.reads[path] = (digest, context)
        self.reads.move_to_end(path)
        while len(self.reads) > MAX_READS:
            self.reads.popitem(last=False)

    def _selection(self, path: Path) -> tuple[str, FormatRules]:
        return (
            self.policy.select(path)
            if self.policy is not None
            else ("translate", FormatRules())
        )

    def _context_hash(self, raw: bytes | None, action: str, rules: FormatRules) -> str:
        return sha256(
            json_bytes(
                {
                    "source_sha256": sha256(raw) if raw is not None else None,
                    "dictionary_sha256": self.dictionary.fingerprint,
                    "policy_sha256": self.policy.fingerprint if self.policy else None,
                    "action": action,
                    "document": rules.document(),
                    "marker_version": self.cipher.marker_version,
                }
            )
        )

    def public_rules(self, action: str, rules: FormatRules) -> dict[str, Any]:
        """Describe editable selections using only the vocabulary visible to agents."""
        if action == "copy":
            return {"format": "copy"}
        codec = DocumentCodec(
            self.dictionary, rules, marker_version=self.cipher.marker_version
        )
        return codec.python.public_rules() if codec.python else rules.document()

    def read(self, file_path: str, *, remember: bool = True) -> dict[str, Any]:
        with self.lock:
            path = self._path(file_path)
            action, rules = self._selection(path)
            raw = self.workspace.read(path)
            encoded = (
                raw.decode("utf-8")
                if action == "copy"
                else self.cipher.encode_document(raw.decode("utf-8"), rules).text
            )
            if len(encoded.encode("utf-8")) > self.workspace.max_bytes:
                raise ValueError("Encoded agent content exceeds the file limit")
            digest = sha256(raw)
            context_hash = self._context_hash(raw, action, rules)
            if self.policy is not None:
                self.policy.recheck()
            if self.workspace.read(path) != raw:
                raise ValueError("Source changed during translation; read again")
            if remember:
                self._remember(path, digest, context_hash)
            return {
                "status": "read",
                "file_path": path.relative_to(self.workspace.root).as_posix(),
                "content": encoded,
                "source_sha256": digest,
                "dictionary_sha256": self.dictionary.fingerprint,
                "marker_version": self.cipher.marker_version,
                "context_sha256": context_hash,
                "neutral_sha256": sha256(encoded.encode("utf-8")),
                "document_format": rules.format if action != "copy" else "copy",
                "document_rules": self.public_rules(action, rules),
                "terminology_contract": "Edit or generate code in this vocabulary. Preserve __SCM_LIT_...__, __SCM_CASE_...__, and __SCM_CASE_x_<hex>__ prefix escapes exactly. For newly authored literal marker syntax use bamboozle.markers.quote_prefixes on the raw snippet only; its payload contains only a reserved prefix. Submit neutral content to write_safe_file; the cipher restores domain terms locally.",
            }

    def write(
        self,
        file_path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_context_sha256: str | None = None,
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
        with self.lock:
            path = self._path(file_path, write=True)
            action, rules = self._selection(path)
            if (
                ledger_path(path).exists()
                or path.with_name(f".{path.name}.vigiles-restore.json").exists()
            ):
                raise ValueError(
                    "Destination has translation metadata; choose a source file or new output"
                )
            before = self.workspace.read(path) if path.exists() else None
            remembered = self.reads.get(path)
            expected = (
                expected_sha256
                if expected_sha256 is not None
                else remembered[0]
                if remembered
                else None
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
            context_hash = self._context_hash(before, action, rules)
            expected_context = (
                expected_context_sha256
                if expected_context_sha256 is not None
                else remembered[1]
                if remembered
                else None
            )
            if expected_context is not None and expected_context != context_hash:
                raise ValueError(
                    "Translation context changed; read again before writing"
                )
            if (
                self.policy is not None
                and before is not None
                and expected_context is None
            ):
                raise ValueError(
                    "Configured source writes require context_sha256 from read_safe_file after reconnecting"
                )
            codec = DocumentCodec(
                self.dictionary,
                rules,
                limit=self.workspace.max_bytes,
                marker_version=self.cipher.marker_version,
            )
            context = (
                codec.transform(before.decode("utf-8")).context
                if before is not None and action == "translate"
                else {}
            )
            decoded = (
                content
                if action == "copy"
                else codec.transform(content, "reverse", context=context).text
            ).encode("utf-8")
            if len(decoded) > self.workspace.max_bytes:
                raise ValueError("Decoded agent content exceeds the file limit")
            # Validate the resulting document before any source/history publication.
            if action == "translate":
                codec.transform(decoded.decode("utf-8"))
            if self.policy is not None:
                self.policy.recheck()
            next_context = self._context_hash(decoded, action, rules)
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
                "context_sha256": next_context,
                "document_format": rules.format if action != "copy" else "copy",
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
                            "read_context_sha256": context_hash,
                            "translation": {
                                "dictionary": self.dictionary.document(),
                                "document": rules.document(),
                                "action": action,
                                "document_context": context,
                                "policy_sha256": self.policy.fingerprint
                                if self.policy
                                else None,
                            },
                        }
                    ),
                )
                path = self._path(file_path, write=True)
                if self.policy is not None:
                    self.policy.recheck()
                current = self.workspace.read(path) if path.exists() else None
                if current != before:
                    raise ValueError(
                        "Source changed before publication; retained history is available"
                    )
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path = self._path(file_path, write=True)
                    atomic_write(path, decoded, overwrite=before is not None)
                except PublicationError:
                    report["output_saved"] = True
                    raise
                report["output_saved"] = True
                if self.workspace.read(path) != decoded:
                    raise ValueError(
                        "Source changed after publication; inspect the retained history"
                    )
                if self.policy is not None:
                    self.policy.recheck()
                self._remember(path, sha256(decoded), next_context)
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
