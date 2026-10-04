"""Reviewed multi-file return from Bamboozle views with explicit resumable imports."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from bamboozle.dictionary import Dictionary
from bamboozle.fixtures import TEMPLATE_SCHEMA, validate_contract
from bamboozle.formats import DocumentCodec
from bamboozle.project_exchange import returned_snapshot
from bamboozle.project_views import (
    GENERATED_FILES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    blob,
    digest,
    document_rules,
    editable_path,
    generated_contracts,
    load_view,
    manifest_marker_version,
    operation_lock,
    put_blob,
    read_optional,
    relative_name,
    snapshot_view,
    unique_paths,
)
from bamboozle.python_project import validate_project
from bamboozle.restoration import _diff
from bamboozle.return_validation import inspect_return
from bamboozle.service import OperationError
from bamboozle.utils import (
    atomic_write,
    exclusive_lock,
    json_bytes,
    parse_json,
    read_bytes,
    sha256,
    utc_now,
)
from bamboozle.workspace import Workspace


def _deletions(values: list[str] | None) -> list[str]:
    if values is not None and (not isinstance(values, list) or len(values) > MAX_FILES):
        raise ValueError("delete_files must be a bounded list of relative file paths")
    names = [relative_name(name) for name in values or []]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate deletion selection")
    return sorted(names)


def _snapshot(
    workspace: Workspace,
    manifest: dict[str, Any],
    manifest_sha: str,
    view: Workspace,
    packet: str | None,
    key: str | None,
    recipient: str | None,
    recipe: str | None,
) -> tuple[dict[str, bytes], str | None]:
    if packet is None:
        if any(value is not None for value in (key, recipient, recipe)):
            raise ValueError("Exchange credentials require a return packet")
        return snapshot_view(view), None
    if key is None or recipient is None:
        raise ValueError("A return packet requires an explicit key and recipient")
    return returned_snapshot(
        workspace,
        manifest,
        manifest_sha,
        packet,
        key=key,
        recipient=recipient,
        recipe=recipe,
    )


def _unchanged_return(
    workspace: Workspace,
    view: Workspace,
    plan: dict[str, Any],
    packet: str | None,
) -> bool:
    if "exchange_sha256" in plan:
        # Recovery uses the authenticated snapshot retained before source writes.
        return (
            packet is None
            or sha256(read_bytes(workspace.resolve(packet), workspace.max_bytes * 2))
            == plan["exchange_sha256"]
        )
    return _hashes(snapshot_view(view)) == plan["view_snapshot"]


def _hashes(snapshot: dict[str, bytes]) -> dict[str, str]:
    return {name: sha256(raw) for name, raw in snapshot.items()}


def _build(
    workspace: Workspace,
    manifest: dict[str, Any],
    manifest_sha: str,
    mapping: Dictionary,
    view: Workspace,
    directory: Path,
    snapshot: dict[str, bytes],
    delete_files: list[str],
    *,
    validation: list[dict[str, Any]] | None = None,
    validation_path: str | None = None,
    validation_offset: int = 0,
    max_findings: int = 100,
) -> tuple[
    dict[str, Any], dict[str, bytes], list[dict[str, Any]], list[dict[str, str]]
]:
    controls = generated_contracts(manifest)
    if any(
        name not in snapshot or sha256(snapshot[name]) != expected
        for name, expected in controls.items()
    ):
        raise ValueError("Generated view contract was removed or changed")
    expected: dict[str, str | None] = {}
    outputs: dict[str, bytes] = {}
    actions: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    changes: list[dict[str, Any]] = []
    selected_deletions = set(delete_files)
    consumed_deletions: set[str] = set()
    total = 0
    diff_remaining = 262144
    python_modules: dict[str, tuple[str, ...]] = {}
    decoded_snapshot: dict[str, bytes] = {}
    finding_remaining = 200
    names = sorted(set(manifest["files"]) | (set(snapshot) - set(controls)))
    unique_paths(names)
    if len(names) > MAX_FILES:
        raise ValueError("Import exceeds its file limit")
    for name in names:
        entry = manifest["files"].get(name)
        policy = entry["policy"] if entry else None
        rule = manifest["policy"]["files"].get(name, {})
        if policy == "fixture":
            if "rules" in rule:
                # Code returning from a generated fixture was qualified against
                # these production records; data drift needs a fresh preparation.
                expected[name] = entry["original_sha256"]
                if name not in snapshot:
                    conflicts.append(
                        {
                            "path": name,
                            "reason": "Generated fixture is missing; restore it or prepare a fresh view",
                        }
                    )
                else:
                    try:
                        selected = parse_json(snapshot[TEMPLATE_SCHEMA])["fixtures"][
                            name
                        ]
                        checked = validate_contract(
                            snapshot[name], selected, limit=workspace.max_bytes
                        )
                        if not checked["ok"]:
                            conflicts.append(
                                {
                                    "path": name,
                                    "reason": "Generated fixture violates its retained contract: "
                                    + ", ".join(checked["codes"]),
                                }
                            )
                    except (ValueError, TypeError) as exc:
                        conflicts.append({"path": name, "reason": str(exc)})
            if name not in snapshot or sha256(snapshot[name]) != entry["view_sha256"]:
                changes.append(
                    {
                        "path": name,
                        "action": "fixture_ignored",
                        "reason": "Fixture edits never replace authoritative source",
                    }
                )
            continue
        try:
            editable_path(workspace, name, manifest["private_paths"], view.root)
        except (ValueError, OSError) as exc:
            conflicts.append({"path": name, "reason": str(exc)})
            continue
        if entry is None:
            if name in manifest["policy"]["files"]:
                conflicts.append(
                    {
                        "path": name,
                        "reason": "Explicitly excluded path cannot be imported",
                    }
                )
                continue
            candidates = [
                prefix
                for prefix in manifest["policy"]["new_files"]
                if prefix == "."
                or PurePosixPath(name).is_relative_to(PurePosixPath(prefix))
            ]
            if not candidates:
                conflicts.append(
                    {
                        "path": name,
                        "reason": "New file is outside the allowed directories",
                    }
                )
                continue
            prefix = max(candidates, key=lambda value: len(PurePosixPath(value).parts))
            rule = manifest["policy"]["new_files"][prefix]
            policy = rule["policy"] if isinstance(rule, dict) else rule
        before = blob(workspace, directory, entry["original_sha256"]) if entry else None
        if (
            policy == "translate"
            and name in snapshot
            and document_rules(rule).format == "python"
        ):
            python_modules[name] = document_rules(rule).modules
        expected[name] = sha256(before) if before is not None else None
        if name not in snapshot:
            if name not in selected_deletions:
                conflicts.append(
                    {
                        "path": name,
                        "reason": "Missing file requires explicit delete_files selection",
                    }
                )
                continue
            consumed_deletions.add(name)
            decoded = None
        else:
            try:
                text = snapshot[name].decode("utf-8")
                if (
                    policy == "translate"
                    and validation is not None
                    and (validation_path is None or name == validation_path)
                ):
                    checked_return = inspect_return(
                        snapshot[name],
                        mapping,
                        baseline=blob(workspace, directory, entry["view_sha256"])
                        if entry
                        else None,
                        rules=document_rules(rule),
                        context=entry.get("document_context", {}) if entry else {},
                        limit=workspace.max_bytes,
                        marker_version=manifest_marker_version(manifest),
                        offset=validation_offset,
                        max_findings=max_findings
                        if validation_path is not None
                        else min(finding_remaining, 100),
                    )
                    finding_remaining -= len(checked_return["findings"])
                    validation.append({"path": name, **checked_return})
                decoded = (
                    DocumentCodec(
                        mapping,
                        document_rules(rule),
                        limit=workspace.max_bytes,
                        marker_version=manifest_marker_version(manifest),
                    )
                    .transform(
                        text,
                        "reverse",
                        context=entry.get("document_context", {}) if entry else {},
                    )
                    .text.encode("utf-8")
                    if policy == "translate"
                    else snapshot[name]
                )
            except ValueError as exc:
                conflicts.append({"path": name, "reason": str(exc)})
                continue
            decoded_snapshot[name] = decoded
            total += len(decoded)
            if len(decoded) > workspace.max_bytes or total > MAX_TOTAL_BYTES:
                raise ValueError("Decoded project exceeds its byte limit")
        if decoded == before:
            continue
        action = {
            "path": name,
            "policy": policy,
            "action": "delete"
            if decoded is None
            else "create"
            if before is None
            else "modify",
            "before_sha256": expected[name],
            "output_sha256": sha256(decoded) if decoded is not None else None,
            "output_bytes": len(decoded) if decoded is not None else 0,
        }
        actions.append(action)
        if decoded is not None:
            outputs[name] = decoded
        difference = _diff(before or b"", decoded or b"")
        size = len(difference["diff"].encode("utf-8"))
        if size > diff_remaining:
            difference = {
                "diff": "",
                "diff_complete": False,
                "diff_reason": "Project diff limit exceeded",
            }
        else:
            diff_remaining -= size
        changes.append({**action, **difference})
    if selected_deletions != consumed_deletions:
        raise ValueError(
            "Deletion selection must name missing editable baseline files only"
        )
    if python_modules and not conflicts:
        try:
            validate_project(snapshot, python_modules)
            validate_project(decoded_snapshot, python_modules)
        except ValueError as exc:
            conflicts.append({"path": "<python-project>", "reason": str(exc)})
    plan = {
        "view_id": manifest["view_id"],
        "manifest_sha256": manifest_sha,
        "view_snapshot": {name: sha256(raw) for name, raw in snapshot.items()},
        "source_snapshot": expected,
        "delete_files": delete_files,
        "actions": actions,
    }
    return plan, outputs, changes, conflicts


def _observations(
    workspace: Workspace,
    manifest: dict[str, Any],
    view: Workspace,
    plan: dict[str, Any],
) -> dict[str, str | None]:
    return {
        name: sha256(raw)
        if (
            raw := read_optional(
                workspace,
                editable_path(workspace, name, manifest["private_paths"], view.root),
            )
        )
        is not None
        else None
        for name in plan["source_snapshot"]
    }


def review_import(
    workspace: Workspace,
    view_id: str,
    *,
    delete_files: list[str] | None = None,
    validation_path: str | None = None,
    validation_offset: int = 0,
    max_findings: int = 100,
    return_packet: str | None = None,
    key: str | None = None,
    recipient: str | None = None,
    recipe: str | None = None,
) -> dict[str, Any]:
    if (
        type(validation_offset) is not int
        or validation_offset < 0
        or type(max_findings) is not int
        or not 1 <= max_findings <= 500
    ):
        raise ValueError(
            "validation_offset must be nonnegative; max_findings must be 1 to 500"
        )
    if validation_path is None and (validation_offset != 0 or max_findings != 100):
        raise ValueError("Diagnostic pagination requires validation_path")
    if validation_path is not None:
        relative_name(validation_path)
    directory, manifest, manifest_sha, mapping, view = load_view(workspace, view_id)
    if (directory / "import.json").exists():
        raise ValueError(
            "View already has an import; inspect its status or explicitly recover it"
        )
    snapshot, exchange_sha = _snapshot(
        workspace, manifest, manifest_sha, view, return_packet, key, recipient, recipe
    )
    validation: list[dict[str, Any]] = []
    plan, _, changes, conflicts = _build(
        workspace,
        manifest,
        manifest_sha,
        mapping,
        view,
        directory,
        snapshot,
        _deletions(delete_files),
        validation=validation,
        validation_path=validation_path,
        validation_offset=validation_offset,
        max_findings=max_findings,
    )
    if exchange_sha is not None:
        plan["exchange_sha256"] = exchange_sha
    if validation_path is not None and not validation:
        raise ValueError(
            "validation_path must select a present UTF-8 translated return file"
        )
    observed = _observations(workspace, manifest, view, plan)
    for name, current in observed.items():
        if current != plan["source_snapshot"][name]:
            conflicts.append(
                {
                    "path": name,
                    "reason": "Authoritative source changed since export, or new destination already exists",
                }
            )
    if (
        not _unchanged_return(workspace, view, plan, return_packet)
        or load_view(workspace, view_id)[2] != manifest_sha
        or _observations(workspace, manifest, view, plan) != observed
    ):
        raise ValueError(
            "Source, view or retained context changed during review; review again"
        )
    return {
        "schema_version": 1,
        "view_id": view_id,
        "status": "conflict" if conflicts else "review",
        **({"exchange_sha256": exchange_sha} if exchange_sha is not None else {}),
        "ok": not conflicts,
        "changes": changes,
        "return_validation": {
            "status": "refused"
            if conflicts or any(item["status"] == "refused" for item in validation)
            else "needs_review"
            if any(item["status"] == "needs_review" for item in validation)
            else "accepted",
            "files": validation,
            "scope": validation_path
            if validation_path is not None
            else "all_translated_returns",
            "finding_count": sum(item["finding_count"] for item in validation),
            "findings_complete": all(item["findings_complete"] for item in validation),
            "analysis_complete": all(item["analysis_complete"] for item in validation),
            "advisory": "Diagnostics do not decide edit intent; inspect decoded diffs before applying the exact review digest.",
        },
        "conflicts": conflicts,
        "review_sha256": None if conflicts else sha256(json_bytes(plan)),
        "action_count": len(plan["actions"]),
        "output_saved": False,
    }


def _load_import(
    workspace: Workspace, view_id: str
) -> tuple[Path, dict[str, Any], Workspace, dict[str, Any], dict[str, bytes]]:
    directory, manifest, manifest_sha, mapping, view = load_view(workspace, view_id)
    document = parse_json(workspace.read(directory / "import.json"))
    if (
        not isinstance(document, dict)
        or set(document)
        != {"schema_version", "kind", "created", "plan", "review_sha256"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["kind"] != "bamboozle.view-import"
        or not isinstance(document["plan"], dict)
    ):
        raise ValueError("Invalid retained import journal")
    plan = document["plan"]
    if sha256(json_bytes(plan)) != digest(document["review_sha256"]):
        raise ValueError("Retained import digest mismatch")
    if not isinstance(plan.get("view_snapshot"), dict) or len(
        plan["view_snapshot"]
    ) > MAX_FILES + 1 + len(GENERATED_FILES):
        raise ValueError("Invalid retained view snapshot")
    snapshot = {}
    total = 0
    for name, fingerprint in plan["view_snapshot"].items():
        relative_name(name)
        raw = blob(workspace, directory, fingerprint)
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Retained view exceeds its byte limit")
        snapshot[name] = raw
    rebuilt, outputs, _, conflicts = _build(
        workspace,
        manifest,
        manifest_sha,
        mapping,
        view,
        directory,
        snapshot,
        _deletions(plan.get("delete_files")),
    )
    if "exchange_sha256" in plan:
        rebuilt["exchange_sha256"] = digest(plan["exchange_sha256"])
    if conflicts or json_bytes(rebuilt) != json_bytes(plan):
        raise ValueError("Import no longer matches its retained source/view snapshots")
    for name, data in outputs.items():
        if blob(workspace, directory, sha256(data)) != data:
            raise ValueError(f"Decoded snapshot changed: {name}")
    return directory, manifest, view, document, outputs


def _receipt(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "bamboozle.view-import-complete",
        "schema_version": 1,
        "view_id": plan["view_id"],
        "review_sha256": sha256(json_bytes(plan)),
        "manifest_sha256": plan["manifest_sha256"],
        "actions": plan["actions"],
        **(
            {"exchange_sha256": plan["exchange_sha256"]}
            if "exchange_sha256" in plan
            else {}
        ),
    }


def _states(
    plan: dict[str, Any], observations: dict[str, str | None]
) -> dict[str, str]:
    after = {action["path"]: action["output_sha256"] for action in plan["actions"]}
    return {
        name: "applied"
        if name in after and current == after[name]
        else "unchanged"
        if name not in after and current == plan["source_snapshot"][name]
        else "pending"
        if current == plan["source_snapshot"][name]
        else "conflict"
        for name, current in observations.items()
    }


def view_status(workspace: Workspace, view_id: str) -> dict[str, Any]:
    directory, manifest, _, _, view = load_view(workspace, view_id)
    if not (directory / "import.json").exists():
        review = review_import(workspace, view_id)
        return {
            key: value
            for key, value in {
                **review,
                "status": "conflict" if not review["ok"] else "ready",
            }.items()
            if key != "changes"
        }
    directory, manifest, view, document, _ = _load_import(workspace, view_id)
    states = _states(
        document["plan"], _observations(workspace, manifest, view, document["plan"])
    )
    receipt_path = workspace.resolve(directory / "result.json")
    complete = receipt_path.exists()
    if complete and parse_json(workspace.read(receipt_path)) != _receipt(
        document["plan"]
    ):
        raise ValueError("Invalid completed import receipt")
    exchanged = "exchange_sha256" in document["plan"]
    return {
        "schema_version": 1,
        "view_id": view_id,
        "status": "complete" if complete else "pending",
        "review_sha256": document["review_sha256"],
        "files": states,
        "view_changed": None
        if exchanged
        else not _unchanged_return(workspace, view, document["plan"], None),
        **(
            {
                "exchange_sha256": document["plan"]["exchange_sha256"],
                "return_source": "retained_authenticated_snapshot",
            }
            if exchanged
            else {}
        ),
        "receipt_saved": complete,
    }


def apply_import(
    workspace: Workspace,
    view_id: str,
    review_sha256: str,
    *,
    delete_files: list[str] | None = None,
    recover: bool = False,
    return_packet: str | None = None,
    key: str | None = None,
    recipient: str | None = None,
    recipe: str | None = None,
) -> dict[str, Any]:
    digest(review_sha256)
    if recover and any(
        value is not None for value in (return_packet, key, recipient, recipe)
    ):
        raise ValueError(
            "Recovery uses retained snapshots; omit packet and credentials"
        )
    with exclusive_lock(operation_lock(workspace)):
        directory, manifest, manifest_sha, mapping, view = load_view(workspace, view_id)
        journal_path = workspace.resolve(directory / "import.json", write=True)
        receipt_path = workspace.resolve(directory / "result.json", write=True)
        if receipt_path.exists():
            raise ValueError(
                "Import already completed; export a fresh view for another development cycle"
            )
        if journal_path.exists() != recover:
            raise ValueError(
                "Existing imports require explicit recovery; recovery requires a retained journal"
            )
        report: dict[str, Any] = {
            "schema_version": 1,
            "view_id": view_id,
            "review_sha256": review_sha256,
            "journal_saved": recover,
            "receipt_saved": False,
            "output_saved": False,
            "applied_files": [],
        }
        plan: dict[str, Any] | None = None
        try:
            if recover:
                if delete_files:
                    raise ValueError("Recovery uses the saved deletion decisions")
                directory, manifest, view, document, outputs = _load_import(
                    workspace, view_id
                )
                plan = document["plan"]
                if document["review_sha256"] != review_sha256:
                    raise ValueError(
                        "Recovery digest does not match the retained import"
                    )
            else:
                snapshot, exchange_sha = _snapshot(
                    workspace,
                    manifest,
                    manifest_sha,
                    view,
                    return_packet,
                    key,
                    recipient,
                    recipe,
                )
                plan, outputs, _, conflicts = _build(
                    workspace,
                    manifest,
                    manifest_sha,
                    mapping,
                    view,
                    directory,
                    snapshot,
                    _deletions(delete_files),
                )
                if exchange_sha is not None:
                    plan["exchange_sha256"] = exchange_sha
                if conflicts or sha256(json_bytes(plan)) != review_sha256:
                    raise ValueError("Review is stale or contains unresolved conflicts")
                if (
                    _observations(workspace, manifest, view, plan)
                    != plan["source_snapshot"]
                ):
                    raise ValueError("Authoritative source changed after review")
                if not plan["actions"]:
                    return {**report, "status": "no_changes"}
                for raw in [*snapshot.values(), *outputs.values()]:
                    put_blob(workspace, directory, raw)
                document = {
                    "schema_version": 1,
                    "kind": "bamboozle.view-import",
                    "created": utc_now(),
                    "plan": plan,
                    "review_sha256": review_sha256,
                }
                journal = json_bytes(document)
                if len(journal) > workspace.max_bytes:
                    raise ValueError("Import journal exceeds its file limit")
                atomic_write(journal_path, journal)
                report["journal_saved"] = True
            assert plan is not None
            if not _unchanged_return(workspace, view, plan, return_packet):
                raise ValueError(
                    "View changed after review; restore the retained view before recovery"
                )
            if (
                sha256(workspace.read(directory / "manifest.json"))
                != plan["manifest_sha256"]
            ):
                raise ValueError("View manifest changed after review")
            observations = _observations(workspace, manifest, view, plan)
            states = _states(plan, observations)
            if "conflict" in states.values():
                raise ValueError(
                    "Source conflicts with the retained import; inspect view-status"
                )
            for action in plan["actions"]:
                name = action["path"]
                path = editable_path(
                    workspace, name, manifest["private_paths"], view.root
                )
                source_raw = read_optional(workspace, path)
                observed = sha256(source_raw) if source_raw is not None else None
                if observed != action["output_sha256"]:
                    if observed != action["before_sha256"]:
                        raise ValueError(f"Source changed before publication: {name}")
                    if action["action"] == "delete":
                        path.unlink()
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path = editable_path(
                            workspace, name, manifest["private_paths"], view.root
                        )
                        atomic_write(
                            path,
                            outputs[name],
                            overwrite=action["before_sha256"] is not None,
                        )
                report["applied_files"].append(name)
                report["output_saved"] = True
            expected = {
                **plan["source_snapshot"],
                **{
                    action["path"]: action["output_sha256"]
                    for action in plan["actions"]
                },
            }
            if _observations(workspace, manifest, view, plan) != expected:
                raise ValueError(
                    "Source changed during import; completion was withheld"
                )
            if not _unchanged_return(workspace, view, plan, return_packet):
                raise ValueError("View changed during import; completion was withheld")
            atomic_write(receipt_path, json_bytes(_receipt(plan)))
            report.update(
                status="recovered" if recover else "applied", receipt_saved=True
            )
            return report
        except (OSError, ValueError, TypeError) as exc:
            report.update(
                status="error",
                error=str(exc),
                journal_saved=journal_path.is_file(),
                receipt_saved=receipt_path.is_file(),
            )
            if journal_path.is_file() and plan is not None:
                try:
                    states = _states(
                        plan, _observations(workspace, manifest, view, plan)
                    )
                    report["files"] = states
                    report["applied_files"] = [
                        name for name, state in states.items() if state == "applied"
                    ]
                    report["output_saved"] = bool(report["applied_files"])
                except (OSError, ValueError):
                    report["observation_incomplete"] = True
            raise OperationError(str(exc), report) from exc
