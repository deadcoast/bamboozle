"""Explicit repository structure rules and prospective source-publication guards.

Profiles are operator inputs. Evaluation never executes project code or rewrites
source. A guard binds the active profile and the complete proposed scoped tree;
overlay evaluation also works across a partially published recoverable import.
"""

from __future__ import annotations

import re
import stat
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from bamboozle.utils import (
    MAX_FILE_BYTES,
    atomic_write,
    json_bytes,
    parse_json,
    sha256,
    utc_now,
)
from bamboozle.workspace import PROTECTED, Workspace

PROFILE = Path(".vigiles/structure.json")
HISTORY = Path(".vigiles/structure/refusals")
MAX_ENTRIES = 10000
MAX_TOTAL = 64 * 1024 * 1024
MAX_FINDINGS = 2000
PRIVATE_DIRS = PROTECTED | {".vigiles", ".mivim", ".local"}
FORMATS = {"text", "python", "json", "yaml"}
METADATA_SUFFIXES = (".scm_ledger.json", ".vigiles-restore.json")


def _name(value: Any, *, root: bool = False) -> str:
    if root and value == ".":
        return value
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("Structure paths must be bounded relative POSIX names")
    parts = value.split("/")
    if any(
        not part
        or part in (".", "..")
        or part[-1:] in (" ", ".")
        or part.casefold() in PRIVATE_DIRS
        or re.search(r'[\\:<>"|?*\x00-\x1f]', part)
        for part in parts
    ):
        raise ValueError(
            "Structure paths cannot enter private state or use ambiguous path syntax"
        )
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    if any(part.split(".")[0].casefold() in reserved for part in parts):
        raise ValueError("Structure paths cannot use reserved device names")
    return value


def _within(name: str, root: str) -> bool:
    return root == "." or name == root or name.startswith(root + "/")


def _ignored(document: dict[str, Any], name: str) -> bool:
    return (
        any(part.casefold() in PRIVATE_DIRS for part in PurePosixPath(name).parts)
        or name.casefold().endswith(METADATA_SUFFIXES)
        or any(
            _within(name.casefold(), value.casefold()) for value in document["exclude"]
        )
    )


def _integer(value: Any, maximum: int, name: str) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 1 to {maximum}")


def _suffix(value: Any) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"\.[a-z0-9][a-z0-9._-]{0,31}", value) is None
    ):
        raise ValueError("Suffixes require a lowercase dot-prefixed file extension")


def rules(raw: bytes) -> dict[str, Any]:
    try:
        document = parse_json(raw)
    except (ValueError, RecursionError) as exc:
        raise ValueError(
            "Structural profile must be strict UTF-8 JSON without duplicate keys or excessive nesting"
        ) from exc
    if not isinstance(document, dict) or set(document) != {
        "kind",
        "schema_version",
        "name",
        "roots",
        "required",
        "companions",
        "exclude",
    }:
        raise ValueError(
            "Structure profile requires kind, schema_version, name, roots, required, companions and exclude"
        )
    if (
        document["kind"] != "bamboozle.structure"
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
    ):
        raise ValueError("Unsupported structural profile version")
    if (
        not isinstance(document["name"], str)
        or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", document["name"]) is None
    ):
        raise ValueError("Structure profile name must be a lowercase identifier")
    roots = document["roots"]
    if not isinstance(roots, dict) or not 1 <= len(roots) <= 32:
        raise ValueError("Structure profile needs 1 to 32 roots")
    for name, rule in roots.items():
        _name(name, root=True)
        if any(
            other != name
            and (
                _within(name.casefold(), other.casefold())
                or _within(other.casefold(), name.casefold())
            )
            for other in roots
        ):
            raise ValueError(
                "Structure roots must be disjoint with unambiguous spelling"
            )
        if not isinstance(rule, dict) or set(rule) != {
            "suffixes",
            "formats",
            "max_files",
            "max_file_bytes",
            "max_total_bytes",
        }:
            raise ValueError(
                "Each root needs suffixes, formats and explicit file/count/total byte limits"
            )
        suffixes = rule["suffixes"]
        if not isinstance(suffixes, list) or not 1 <= len(suffixes) <= 32:
            raise ValueError("Each root requires 1 to 32 allowed suffixes")
        for suffix in suffixes:
            _suffix(suffix)
        if len(set(suffixes)) != len(suffixes):
            raise ValueError("Duplicate allowed suffix")
        if (
            not isinstance(rule["formats"], dict)
            or not set(rule["formats"]) <= set(suffixes)
            or any(
                not isinstance(value, str) or value not in FORMATS
                for value in rule["formats"].values()
            )
        ):
            raise ValueError(
                "Format checks must map allowed suffixes to text, python, json or yaml"
            )
        for key, maximum in (
            ("max_files", MAX_ENTRIES),
            ("max_file_bytes", MAX_FILE_BYTES),
            ("max_total_bytes", MAX_TOTAL),
        ):
            _integer(rule[key], maximum, key)
    excluded = document["exclude"]
    if not isinstance(excluded, list) or len(excluded) > 128:
        raise ValueError("exclude must contain at most 128 directory paths")
    for name in excluded:
        _name(name)
        if not any(_within(name, root) and name != root for root in roots):
            raise ValueError(
                "Excluded directories must be strictly inside a selected root"
            )
    if len({name.casefold() for name in excluded}) != len(excluded):
        raise ValueError("Duplicate excluded directory")
    required = document["required"]
    if not isinstance(required, dict) or len(required) > 512:
        raise ValueError("required must contain at most 512 exact paths")
    for name, kind in required.items():
        _name(name)
        if (
            kind not in ("file", "directory")
            or not any(_within(name, root) for root in roots)
            or any(_within(name.casefold(), prefix.casefold()) for prefix in excluded)
        ):
            raise ValueError(
                "Required paths must select files/directories in the visible profile roots"
            )
    if len({name.casefold() for name in required}) != len(required):
        raise ValueError("Required paths have ambiguous spelling")
    companions = document["companions"]
    if not isinstance(companions, list) or len(companions) > 32:
        raise ValueError("companions must be a list of at most 32 rules")
    for rule in companions:
        if not isinstance(rule, dict) or set(rule) != {
            "source",
            "source_suffix",
            "target",
            "target_suffix",
            "target_prefix",
            "exclude_names",
        }:
            raise ValueError(
                "Companions need source/target roots, suffixes, target_prefix and exclude_names"
            )
        if any(
            not isinstance(rule[key], str) or rule[key] not in roots
            for key in ("source", "target")
        ):
            raise ValueError("Companion endpoints must be declared roots")
        for key in ("source_suffix", "target_suffix"):
            _suffix(rule[key])
            if (
                rule[key]
                not in roots[rule["source" if key == "source_suffix" else "target"]][
                    "suffixes"
                ]
            ):
                raise ValueError("Companion suffix must be allowed by its root")
        if (
            not isinstance(rule["target_prefix"], str)
            or re.fullmatch(r"[a-z0-9_]{0,32}", rule["target_prefix"]) is None
        ):
            raise ValueError(
                "Companion target_prefix must be a bounded filename prefix"
            )
        if (
            not isinstance(rule["exclude_names"], list)
            or len(rule["exclude_names"]) > 32
        ):
            raise ValueError("Companion exclude_names must be a bounded basename list")
        for name in rule["exclude_names"]:
            if "/" in _name(name):
                raise ValueError("Companion exclusions must be basenames")
    return document


def _read_profile(
    workspace: Workspace, source: str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    path = workspace.resolve(source or PROFILE)
    if source is None and not path.exists():
        return None, None
    raw = workspace.read(path)
    return rules(raw), sha256(raw)


def fingerprint(workspace: Workspace) -> str | None:
    try:
        return _read_profile(workspace)[1]
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise StructureViolation(
            "Active structural profile is unavailable or invalid; run structure-check in operator context"
        ) from exc


def _syntax(raw: bytes, format: str) -> str | None:
    try:
        text = raw.decode("utf-8-sig")
        if format == "python":
            compile(text, "<structural-check>", "exec", dont_inherit=True)
        elif format == "json":
            parse_json(text)
        elif format == "yaml":
            from bamboozle.yaml_source import YamlDocument

            YamlDocument(text)
    except (ValueError, SyntaxError, RecursionError, TypeError):
        # Never expose project content or original vocabulary in parser errors.
        return f"Correct invalid {format} syntax or UTF-8 encoding"
    return None


def _evaluate(
    workspace: Workspace, document: dict[str, Any], changes: dict[str, bytes | None]
) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, str]] = []
    complete = True
    visited = total = 0
    bodies: dict[str, bytes] = {}
    finding_count = 0

    def finding(code: str, path: str, rule: str, message: str) -> None:
        nonlocal finding_count
        finding_count += 1
        if len(findings) < MAX_FINDINGS:
            findings.append(
                {"code": code, "path": path, "rule": rule, "message": message}
            )

    def ignored(name: str) -> bool:
        return _ignored(document, name)

    for root in document["roots"]:
        pending = [workspace.root if root == "." else workspace.root / root]
        while pending:
            path = pending.pop()
            name = path.relative_to(workspace.root).as_posix()
            if ignored(name):
                continue
            visited += 1
            if visited > MAX_ENTRIES:
                finding(
                    "scan_limit",
                    root,
                    root,
                    "Narrow the declared roots; the 10000-entry scan bound was reached",
                )
                complete = False
                break
            try:
                # Resolve ancestors too, so a root beneath a junction is refused.
                if name != ".":
                    workspace.resolve(name)
                metadata = path.lstat()
                if path.is_symlink() or path.is_junction():
                    raise ValueError("Link")
                if stat.S_ISDIR(metadata.st_mode):
                    entries[name] = {"kind": "directory"}
                    children = []
                    for child in path.iterdir():
                        children.append(child)
                        if len(children) + len(pending) + visited > MAX_ENTRIES:
                            finding(
                                "scan_limit",
                                name,
                                root,
                                "Narrow the declared roots; directory enumeration exceeds the entry bound",
                            )
                            complete = False
                            break
                    pending.extend(sorted(children, reverse=True))
                elif stat.S_ISREG(metadata.st_mode):
                    size = metadata.st_size
                    entries[name] = {"kind": "file", "bytes": size, "sha256": None}
                    # Changed/deleted inputs are owned by the caller's stale-source
                    # checks. Read and validate their proposed bytes below.
                    if name in changes:
                        continue
                    total += size
                    if size > workspace.max_bytes or total > MAX_TOTAL:
                        finding(
                            "read_limit",
                            name,
                            root,
                            "Reduce input sizes or narrow the profile; complete bytes could not be checked",
                        )
                        complete = False
                        continue
                    existing = workspace.read(name)
                    bodies[name] = existing
                    entries[name] = {
                        "kind": "file",
                        "bytes": len(existing),
                        "sha256": sha256(existing),
                    }
                else:
                    finding(
                        "not_regular",
                        name,
                        root,
                        "Replace this special entry with a regular file or directory",
                    )
                    complete = False
            except FileNotFoundError:
                if name != root:
                    finding(
                        "source_changed",
                        name,
                        root,
                        "Entry disappeared during evaluation; retry against stable source",
                    )
                    complete = False
            except (OSError, ValueError):
                finding(
                    "unavailable",
                    name,
                    root,
                    "Remove links or restore read access before evaluation",
                )
                complete = False
        if visited > MAX_ENTRIES:
            break

    for name, raw in changes.items():
        matching = [
            root
            for root in document["roots"]
            if _within(name.casefold(), root.casefold())
        ]
        if not matching or ignored(name):
            continue
        if not any(_within(name, root) for root in matching):
            finding(
                "path_spelling",
                name,
                "portable_paths",
                "Use the exact declared root spelling",
            )
            continue
        if entries.get(name, {}).get("kind") == "directory":
            finding(
                "directory_replacement",
                name,
                "publication",
                "File operations cannot replace directories",
            )
        if raw is None:
            entries.pop(name, None)
        else:
            total += len(raw)
            if total > MAX_TOTAL or len(raw) > workspace.max_bytes:
                finding(
                    "read_limit",
                    name,
                    "publication",
                    "Proposed bytes exceed the profile evaluation limits",
                )
                complete = False
            entries[name] = {"kind": "file", "bytes": len(raw), "sha256": sha256(raw)}
            bodies[name] = raw
            for parent in PurePosixPath(name).parents:
                parent_name = parent.as_posix()
                if any(_within(parent_name, root) for root in document["roots"]):
                    if entries.get(parent_name, {}).get("kind") == "file":
                        finding(
                            "file_parent",
                            parent_name,
                            "publication",
                            "A regular file blocks the proposed parent directory",
                        )
                    entries[parent_name] = {"kind": "directory"}
    if len(entries) > MAX_ENTRIES:
        finding(
            "scan_limit", ".", "publication", "Proposed tree exceeds the entry limit"
        )
        complete = False
    case_names: dict[str, str] = {}
    for name in sorted(entries):
        prior = case_names.setdefault(name.casefold(), name)
        if prior != name:
            finding(
                "case_collision",
                name,
                "portable_paths",
                f"Use one exact spelling; conflicts with {prior}",
            )
    for root, rule in document["roots"].items():
        if entries.get(root, {}).get("kind") == "file":
            finding("root_kind", root, root, "Declared roots must be directories")
        files = {
            name: value
            for name, value in entries.items()
            if _within(name, root) and value["kind"] == "file"
        }
        if len(files) > rule["max_files"]:
            finding(
                "file_count",
                root,
                root,
                f"Reduce file count to {rule['max_files']} or explicitly revise the profile",
            )
        if sum(value["bytes"] for value in files.values()) > rule["max_total_bytes"]:
            finding(
                "total_bytes",
                root,
                root,
                f"Reduce total bytes to {rule['max_total_bytes']} or explicitly revise the profile",
            )
        for name, value in files.items():
            suffix = next(
                (
                    suffix
                    for suffix in sorted(rule["suffixes"], key=len, reverse=True)
                    if name.casefold().endswith(suffix)
                ),
                None,
            )
            if suffix is None:
                finding(
                    "suffix",
                    name,
                    root,
                    "Move this file outside the root or use an explicitly allowed suffix",
                )
            if value["bytes"] > rule["max_file_bytes"]:
                finding(
                    "file_bytes",
                    name,
                    root,
                    f"Reduce this file to {rule['max_file_bytes']} bytes or explicitly revise the profile",
                )
            if suffix in rule["formats"] and name in bodies:
                error = _syntax(bodies[name], rule["formats"][suffix])
                if error:
                    finding("syntax", name, root, error)
    for name, kind in document["required"].items():
        if entries.get(name, {}).get("kind") != kind:
            finding(
                "required",
                name,
                "required",
                f"Provide the required {kind} at this exact path",
            )
    for index, rule in enumerate(document["companions"]):
        for name, value in entries.items():
            if (
                value["kind"] != "file"
                or not _within(name, rule["source"])
                or not name.casefold().endswith(rule["source_suffix"])
                or PurePosixPath(name).name in rule["exclude_names"]
            ):
                continue
            relative = PurePosixPath(name).relative_to(rule["source"])
            stem = relative.name[: -len(rule["source_suffix"])]
            target = (
                PurePosixPath(rule["target"])
                / relative.parent
                / (rule["target_prefix"] + stem + rule["target_suffix"])
            ).as_posix()
            if entries.get(target, {}).get("kind") != "file":
                finding(
                    "companion",
                    name,
                    f"companions/{index}",
                    f"Provide companion file {target}",
                )
    findings.sort(key=lambda row: (row["path"], row["code"], row["rule"]))
    return {
        "ok": complete and not finding_count,
        "complete": complete,
        "entries": len(entries),
        "files": sum(value["kind"] == "file" for value in entries.values()),
        "snapshot_sha256": sha256(json_bytes(entries)),
        "finding_count": finding_count,
        "findings_truncated": finding_count > len(findings),
        "findings": findings,
    }


def check(
    workspace: Workspace,
    source: str | None = None,
    *,
    offset: int = 0,
    limit: int = 100,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    if (
        type(offset) is not int
        or offset < 0
        or type(limit) is not int
        or not 1 <= limit <= 500
    ):
        raise ValueError(
            "Structure pagination requires offset >= 0 and limit from 1 to 500"
        )
    document, digest = _read_profile(workspace, source)
    if document is None:
        if expected_sha256 is not None:
            raise ValueError("Structural profile was removed; restart pagination")
        return {
            "status": "not_configured",
            "configured": False,
            "ok": True,
            "profile": PROFILE.as_posix(),
            "findings": [],
        }
    result = _evaluate(workspace, document, {})
    repeated = _evaluate(workspace, document, {})
    if result != repeated or _read_profile(workspace, source)[1] != digest:
        raise ValueError("Profile or scoped source changed during evaluation; retry")
    identity = sha256(json_bytes({"profile_sha256": digest, **result}))
    if expected_sha256 is not None and expected_sha256 != identity:
        raise ValueError("Structure findings changed; restart pagination")
    all_findings = result.pop("findings")
    return {
        "schema_version": 1,
        "configured": True,
        "status": "valid"
        if result["ok"]
        else "violations"
        if result["complete"]
        else "incomplete",
        "profile": source or PROFILE.as_posix(),
        "profile_sha256": digest,
        "name": document["name"],
        **result,
        "check_sha256": identity,
        "findings": all_findings[offset : offset + limit],
        "findings_complete": offset == 0
        and result["finding_count"] <= limit
        and not result["findings_truncated"],
        "next_offset": offset + limit if offset + limit < len(all_findings) else None,
    }


def validate_certificate(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "profile_sha256", "snapshot_sha256"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("Invalid retained structural certificate")
    for key in ("profile_sha256", "snapshot_sha256"):
        if key == "snapshot_sha256" and value[key] is None:
            continue
        if (
            not isinstance(value[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
        ):
            raise ValueError("Invalid retained structural digest")
    return value


class StructureViolation(ValueError):
    """Public messages contain no source excerpts or unrelated private paths."""


@dataclass
class Guard:
    workspace: Workspace
    changes: dict[str, bytes | None]
    profile_sha256: str | None
    result: dict[str, Any] | None

    @property
    def certificate(self) -> dict[str, Any] | None:
        if self.profile_sha256 is None:
            return None
        return {
            "schema_version": 1,
            "profile_sha256": self.profile_sha256,
            "snapshot_sha256": self.result["snapshot_sha256"] if self.result else None,
        }

    def recheck(self, *, record: bool = True) -> None:
        try:
            current = prepare_guard(self.workspace, self.changes)
        except (OSError, ValueError, TypeError, RecursionError):
            current = None
        stale = current is None or self.certificate != current.certificate
        invalid = (
            current is not None
            and current.result is not None
            and not current.result["ok"]
        )
        if stale or invalid:
            history = None
            if record:
                path = self.workspace.resolve(
                    HISTORY / (uuid.uuid4().hex + ".json"), write=True
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(
                    path,
                    json_bytes(
                        {
                            "schema_version": 1,
                            "created": utc_now(),
                            "status": "stale" if stale else "refused",
                            "expected": self.certificate,
                            "observed": current.certificate if current else None,
                            "changes": {
                                name: sha256(raw) if raw is not None else None
                                for name, raw in self.changes.items()
                            },
                            "evaluation": current.result
                            if current
                            else {
                                "ok": False,
                                "complete": False,
                                "error": "Active profile or scoped source is unavailable or invalid",
                            },
                        }
                    ),
                )
                history = path.relative_to(self.workspace.root).as_posix()
            suffix = f" Retained evidence: {history}" if history else ""
            raise StructureViolation(
                "Structural profile or scoped source changed; recheck the operation."
                + suffix
                if stale
                else "Structural profile refused the proposed source tree; run structure-check for operator diagnostics."
                + suffix
            )

    def pin(self, certificate: dict[str, Any] | None) -> None:
        """Resume against retained intent, without rewriting its historical hash."""
        if certificate is not None:
            validate_certificate(certificate)
        self.profile_sha256 = certificate["profile_sha256"] if certificate else None
        self.result = (
            {"snapshot_sha256": certificate["snapshot_sha256"]}
            if certificate and certificate["snapshot_sha256"] is not None
            else None
        )


def prepare_guard(workspace: Workspace, changes: Mapping[str, bytes | None]) -> Guard:
    document, digest = _read_profile(workspace)
    if len(changes) > MAX_ENTRIES:
        raise ValueError("Proposed changes exceed the structural entry bound")
    for name, raw in changes.items():
        resolved = workspace.resolve(name, write=True)
        if resolved.relative_to(workspace.root).as_posix() != name:
            raise ValueError("Proposed changes require canonical relative POSIX paths")
        if raw is not None and not isinstance(raw, bytes):
            raise TypeError(
                "Structural changes must contain bytes or explicit deletion"
            )
    relevant = document is not None and any(
        any(_within(name.casefold(), root.casefold()) for root in document["roots"])
        and not _ignored(document, name)
        for name in changes
    )
    result = (
        _evaluate(workspace, document, dict(changes))
        if document is not None and relevant
        else None
    )
    if _read_profile(workspace)[1] != digest:
        raise StructureViolation(
            "Structural profile changed during evaluation; recheck the operation"
        )
    return Guard(workspace, dict(changes), digest, result)
