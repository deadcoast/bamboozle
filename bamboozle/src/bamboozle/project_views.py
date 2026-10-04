"""Bamboozle project exports with explicit policies and private source snapshots."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from bamboozle.agent_files import source_path
from bamboozle.dictionary import Dictionary
from bamboozle.fixtures import (
    GENERATED_FILES,
    Fixture,
    artifacts,
    generate_fixture,
    inspect_consumers,
    read_rules,
)
from bamboozle.formats import DocumentCodec, FormatRules
from bamboozle.markers import CURRENT, LEGACY, validate_version
from bamboozle.python_project import validate_project
from bamboozle.service import OperationError, ledger_path
from bamboozle.utils import (
    atomic_write,
    exclusive_lock,
    json_bytes,
    parse_json,
    sha256,
    utc_now,
)
from bamboozle.workspace import PROTECTED, Workspace

STORAGE = Path(".local/bamboozle/views")
CONTRACT = "BAMBOOZLE_VIEW.md"
MAX_FILES = 512
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_NODES = 8192
IGNORED = PROTECTED | {".pytest_cache", ".mypy_cache", ".ruff_cache"}
POLICIES = {"copy", "translate", "fixture", "exclude"}


def relative_name(value: Any, *, directory: bool = False) -> str:
    if directory and value == ".":
        return "."
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("Policy paths must be nonempty relative names")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or len(path.parts) > 32:
        raise ValueError("Policy paths must be canonical relative paths using /")
    devices = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    if any(
        part in {".", ".."}
        or part.rstrip(" .") != part
        or any(ord(char) < 32 or char in '<>:"\\|?*' for char in part)
        or part.split(".")[0].casefold() in devices
        for part in path.parts
    ):
        raise ValueError("Policy path contains an unsafe or nonportable component")
    return value


def digest(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Expected a lowercase SHA-256 digest")
    return value


def unique_paths(names: list[str]) -> None:
    folded = [name.casefold() for name in names]
    if len(set(folded)) != len(folded):
        raise ValueError("Case-insensitive path collision")
    paths = set(folded)
    for name in folded:
        if any(
            parent.as_posix() in paths
            for parent in PurePosixPath(name).parents
            if parent.as_posix() != "."
        ):
            raise ValueError("File and directory paths overlap")


def content_path(
    workspace: Workspace, name: str, private: list[str], *, write: bool = False
) -> Path:
    name = relative_name(name)
    # Reuse the cipher's private-file boundary, with project export exclusions.
    selected = source_path(
        workspace,
        name,
        private_paths={workspace.resolve(item) for item in private},
        write=write,
    )
    lower = selected.name.casefold()
    if (
        lower in {CONTRACT.casefold(), *(name.casefold() for name in GENERATED_FILES)}
        or lower.startswith(".env")
        or lower.endswith((".pem", ".p12", ".pfx"))
    ):
        raise ValueError(
            "Private or generated control files cannot enter a project view"
        )
    return selected


def editable_path(
    workspace: Workspace, name: str, private: list[str], view: Path
) -> Path:
    path = content_path(workspace, name, private, write=True)
    if path.is_relative_to(view):
        raise ValueError("Source destinations cannot enter the generated view")
    if path.is_dir() or any(
        parent.exists() and not parent.is_dir()
        for parent in path.parents
        if parent.is_relative_to(workspace.root)
    ):
        raise ValueError(
            "Source file or parent conflicts with an existing directory/file"
        )
    if (
        ledger_path(path).exists()
        or path.with_name(f".{path.name}.vigiles-restore.json").exists()
    ):
        raise ValueError(
            "Source has separate translation metadata; choose its original file"
        )
    return path


def read_optional(workspace: Workspace, path: Path) -> bytes | None:
    return workspace.read(path) if path.exists() else None


def read_policy(raw: bytes) -> dict[str, Any]:
    data = parse_json(raw)
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "files", "new_files"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] not in (1, 2, 3)
    ):
        raise ValueError(
            "View policy needs schema_version 1, 2 or 3, files, and new_files"
        )
    if (
        not isinstance(data["files"], dict)
        or not 1 <= len(data["files"]) <= MAX_FILES
        or not isinstance(data["new_files"], dict)
        or len(data["new_files"]) > MAX_FILES
    ):
        raise ValueError("View policy exceeds its file or directory limit")
    for name, rule in data["files"].items():
        relative_name(name)
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("policy"), str)
            or rule["policy"] not in POLICIES
        ):
            raise ValueError("Unknown file policy")
        expected = {"policy", "fixture"} if rule["policy"] == "fixture" else {"policy"}
        if (
            rule["policy"] == "fixture"
            and "rules" in rule
            and data["schema_version"] == 3
        ):
            expected = {"policy", "rules"}
            relative_name(rule["rules"])
            if not rule["rules"].casefold().endswith(".fixture-rules.json"):
                raise ValueError(
                    "Generated fixture rules require a private .fixture-rules.json file"
                )
        if (
            rule["policy"] == "translate"
            and "document" in rule
            and data["schema_version"] >= 2
        ):
            expected.add("document")
            FormatRules.from_document(rule["document"])
        if set(rule) != expected:
            raise ValueError("Unexpected file policy fields")
        if "fixture" in rule:
            relative_name(rule["fixture"])
    unique_paths(list(data["files"]))
    roots = []
    for name, rule in data["new_files"].items():
        relative_name(name, directory=True)
        if isinstance(rule, dict) and data["schema_version"] >= 2:
            if set(rule) != {"policy", "document"} or rule["policy"] != "translate":
                raise ValueError(
                    "Structured new-file rules require translate policy and document rules"
                )
            FormatRules.from_document(rule["document"])
        elif not isinstance(rule, str) or rule not in {"copy", "translate"}:
            raise ValueError("New files require copy or translate policy")
        roots.append(name.casefold())
    if len(set(roots)) != len(roots):
        raise ValueError("Case-insensitive new-file policy collision")
    return data


def document_rules(rule: str | dict[str, Any]) -> FormatRules:
    return (
        FormatRules.from_document(rule.get("document"))
        if isinstance(rule, dict)
        else FormatRules()
    )


def consumer_identifier_maps(
    mapping: Dictionary,
    policy: dict[str, Any],
    names: set[str],
    *,
    marker_version: int = CURRENT,
) -> dict[str, dict[str, str]]:
    result = {}
    for name in names:
        rules = document_rules(policy["files"][name])
        if rules.format == "python":
            codec = DocumentCodec(mapping, rules, marker_version=marker_version)
            assert codec.python is not None
            result[name] = codec.python.forward
    return result


def private_directory(workspace: Workspace, view_id: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", view_id) is None:
        raise ValueError("View ID must be 32 lowercase hexadecimal characters")
    return workspace.resolve(STORAGE / view_id / "manifest.json", write=True).parent


def operation_lock(workspace: Workspace) -> Path:
    path = workspace.resolve(STORAGE.parent / "view.lock", write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    return workspace.resolve(path, write=True)


def put_blob(workspace: Workspace, directory: Path, data: bytes) -> str:
    fingerprint = sha256(data)
    path = workspace.resolve(directory / "blobs" / f"{fingerprint}.bin", write=True)
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        if workspace.read(path) != data:
            raise ValueError("Private snapshot has changed")
    else:
        atomic_write(path, data)
    return fingerprint


def blob(workspace: Workspace, directory: Path, fingerprint: str) -> bytes:
    data = workspace.read(directory / "blobs" / f"{digest(fingerprint)}.bin")
    if sha256(data) != fingerprint:
        raise ValueError("Private snapshot digest mismatch")
    return data


def manifest_marker_version(data: dict[str, Any]) -> int:
    if type(data.get("schema_version")) is not int or data["schema_version"] not in (
        1,
        2,
    ):
        raise ValueError("Unsupported project-view manifest version")
    if data["schema_version"] == 1:
        if "marker_version" in data:
            raise ValueError("Legacy view manifests cannot select marker versions")
        return LEGACY
    if validate_version(data.get("marker_version")) != CURRENT:
        raise ValueError("Version-2 view manifests require marker version 2")
    return CURRENT


def contract(
    view_id: str,
    mapping: Dictionary,
    policy: dict[str, Any],
    *,
    marker_version: int = LEGACY,
) -> bytes:
    validate_version(marker_version)

    def public_rule(rule: str | dict[str, Any]) -> str | dict[str, Any]:
        if not isinstance(rule, dict) or "document" not in rule:
            return rule
        codec = DocumentCodec(
            mapping, document_rules(rule), marker_version=marker_version
        )
        return (
            {**rule, "document": codec.python.public_rules()}
            if codec.python is not None
            else rule
        )

    lines = [
        "# Bamboozle development view",
        "",
        f"View ID: `{view_id}`",
        "",
        "Edit this workspace directly. Its contents are already prepared for development.",
        "Do not encode this view again or connect it to an encoder-backed read tool.",
        "Preserve __SCM_LIT_...__ and __SCM_CASE_...__ markers and mapped vocabulary.",
        "Unmarked neutral terms in translated files decode to domain terms on import.",
        "For a listed neutral term intended literally, wrap it as __SCM_LIT_<term>__",
        "(replace <term> with that exact term, without the angle brackets).",
        "Existing case markers encode spelling; keep their entire identifier intact.",
        "The operator reviews and imports changes from the authoritative project.",
        "Fixture edits never replace production files. Missing files require explicit deletion review.",
        "This contract is generated; keep it unchanged.",
        "",
        "## Selected files",
        "",
    ]
    for name, rule in sorted(policy["files"].items()):
        if rule["policy"] != "exclude":
            lines.append(f"- `{name}`: {rule['policy']}")
            if "document" in rule:
                visible = public_rule(rule)
                assert isinstance(visible, dict)
                lines.append(
                    "  Document rules: "
                    + json.dumps(visible["document"], sort_keys=True)
                )
    lines += ["", "## Allowed new-file directories", ""]
    lines.extend(
        f"- `{name}`: {json.dumps(public_rule(rule), sort_keys=True) if isinstance(rule, dict) else rule}"
        for name, rule in sorted(policy["new_files"].items())
    )
    lines += [
        "",
        f"Mapping version: `{mapping.version}`",
        f"Mapping fingerprint: `{mapping.fingerprint}`",
        "",
        "## Neutral vocabulary",
        "",
        "These terms are reversible vocabulary in translated files. Copy files are not decoded.",
        "Only neutral targets are listed; reverse mappings remain with the operator.",
        "",
        "```json",
        json_bytes(sorted(mapping.values())).decode("utf-8").rstrip(),
        "```",
        "",
    ]
    if policy["schema_version"] >= 2:
        lines += [
            "## Document formats",
            "",
            "Apply mapped vocabulary only in the selected content for each file's document rules.",
            "Markdown translates prose; code, complete links, addresses and paths stay literal.",
            "JSON translates the listed exact string-value pointers, or every nested string value when values_only is explicitly true. Keys and nonstring values stay literal.",
            "Exact-pointer rules require their fields to remain present and strings. Recursive values_only rules also translate new string values and report key/type/nonstring changes for review. New files use their directory's rules.",
            "Private JSON spelling context stays with the operator; do not reconstruct or request it.",
            "YAML uses exact string-value paths with the same / and ~0/~1 syntax. Keep every selected path present and string-valued.",
            "Select every alias path that shares a YAML scalar; preserve anchors, tags, keys, comments and unselected data. Merge-owned values cannot be selected.",
            "YAML spelling context also stays private. Generated YAML files use their directory's saved rules.",
            "",
        ]
    if any("rules" in rule for rule in policy["files"].values()):
        lines += [
            "## Generated fixture contracts",
            "",
            "Read TEMPLATE_CONTRACT.md for field definitions, bound processing call paths and verified cases.",
            "TEMPLATE_CONTRACT.json is the public validator input; TEMPLATE_CASES.json contains neutral boundary/error documents.",
            "These files are generated controls; keep them unchanged. Create separate tests for new cases.",
            "Fixture/case text never returns to production source. Run the restored program against original records too.",
            "",
        ]
    if any(
        document_rules(rule).format == "python"
        for rule in [*policy["files"].values(), *policy["new_files"].values()]
    ):
        lines += [
            "## Python source contract",
            "",
            "Python rules list neutral identifier spellings, exact project import paths and separately selected string values.",
            "Preserve these identifiers across bindings, attributes, calls and keyword arguments. New modules use the saved directory rules.",
            "Import paths, external member names, comments and unselected literals remain literal. Only declared project import members translate.",
            "Use explicit external import aliases. Avoid star imports, dynamic namespace evaluation and unresolved reflective member names.",
            "A literal used by getattr, __all__, __slots__, forward annotations or keyword dictionaries must be explicitly selected if it names a translated symbol.",
            "This is explicit project API renaming, not type inference. Run complete dependent programs in this view and after operator restoration.",
            "",
        ]
    if marker_version == CURRENT:
        lines += [
            "## Literal marker syntax (marker version 2)",
            "",
            "Preserve __SCM_CASE_x_<hex>__ prefix escapes exactly. They quote original marker syntax, not domain text.",
            "The lowercase hex must encode only an exact __SCM_LIT_ or __SCM_CASE_ prefix, preserving its original case.",
            "For a newly authored raw marker example, use bamboozle.markers.quote_prefixes on that raw snippet only.",
            "Never quote an already encoded document or replace damaged return markers with guessed literals.",
            "",
        ]
    return "\n".join(lines).encode("utf-8")


def generated_contracts(manifest: dict[str, Any]) -> dict[str, str]:
    """Only explicitly retained generated files are excluded from source import."""
    return {
        CONTRACT: manifest["contract_sha256"],
        **manifest.get("generated_sha256", {}),
    }


def export_view(
    workspace: Workspace,
    config: str,
    output: str,
    dictionary: Dictionary | None = None,
    *,
    private_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    config_path = workspace.resolve(config)
    config_raw = workspace.read(config_path)
    policy = read_policy(config_raw)
    mapping = dictionary if dictionary is not None else Dictionary.load()
    view = workspace.resolve(output, write=True)
    if view.is_relative_to(workspace.root / STORAGE.parent):
        raise ValueError("View must be outside private Bamboozle storage")
    private = sorted(
        {
            config_path.relative_to(workspace.root).as_posix(),
            *(rule["rules"] for rule in policy["files"].values() if "rules" in rule),
            *(
                workspace.resolve(path).relative_to(workspace.root).as_posix()
                for path in private_paths
            ),
        }
    )
    view_id = uuid.uuid4().hex
    directory = private_directory(workspace, view_id)
    originals: dict[str, bytes] = {}
    rendered: dict[str, bytes] = {}
    contexts: dict[str, dict[str, Any]] = {}
    inputs = {config_path.relative_to(workspace.root).as_posix(): sha256(config_raw)}
    fixture_configs: dict[str, bytes] = {}
    generated: dict[str, Fixture] = {}
    total = 0
    for name, rule in policy["files"].items():
        if rule["policy"] == "exclude":
            continue
        source = editable_path(workspace, name, private, view)
        raw = workspace.read(source)
        data = raw
        if rule["policy"] == "translate":
            converted = DocumentCodec(
                mapping, document_rules(rule), limit=workspace.max_bytes
            ).transform(raw.decode("utf-8"))
            data = converted.text.encode("utf-8")
            contexts[name] = converted.context
        elif rule["policy"] == "fixture":
            if "rules" in rule:
                rules_path = workspace.resolve(rule["rules"])
                if rules_path.is_relative_to(view):
                    raise ValueError(
                        "Fixture rules cannot come from the destination view"
                    )
                rules_raw = workspace.read(rules_path)
                if rule["rules"] in inputs and inputs[rule["rules"]] != sha256(
                    rules_raw
                ):
                    raise ValueError("Shared fixture rules changed during preparation")
                total += len(rules_raw)
                rules = read_rules(rules_raw)
                sources = {}
                translated = set()
                for consumer in rules["consumers"]:
                    consumer_name = relative_name(consumer["path"])
                    selected = policy["files"].get(consumer_name, {})
                    if selected.get("policy") not in (
                        "copy",
                        "translate",
                    ) or document_rules(selected).format not in ("text", "python"):
                        raise ValueError(
                            "Fixture consumers must be selected copy/text-translate or explicit Python source files"
                        )
                    consumer_source = workspace.read(
                        editable_path(workspace, consumer_name, private, view)
                    )
                    if consumer_name in inputs and inputs[consumer_name] != sha256(
                        consumer_source
                    ):
                        raise ValueError("Fixture consumer changed during preparation")
                    sources[consumer_name] = consumer_source
                    inputs[consumer_name] = sha256(consumer_source)
                    if selected["policy"] == "translate":
                        translated.add(consumer_name)
                generated[name] = generate_fixture(
                    raw,
                    rules,
                    inspect_consumers(
                        rules,
                        sources,
                        mapping,
                        translated,
                        identifier_maps=consumer_identifier_maps(
                            mapping, policy, translated
                        ),
                    ),
                    limit=workspace.max_bytes,
                )
                data = generated[name].data
                fixture_configs[name] = rules_raw
                inputs[rule["rules"]] = sha256(rules_raw)
            else:
                fixture = content_path(workspace, rule["fixture"], private)
                if fixture.is_relative_to(view):
                    raise ValueError("Fixture cannot come from the destination view")
                data = workspace.read(fixture)
                if rule["fixture"] in inputs and inputs[rule["fixture"]] != sha256(
                    data
                ):
                    raise ValueError(
                        "Shared supplied fixture changed during preparation"
                    )
                inputs[rule["fixture"]] = sha256(data)
        raw.decode("utf-8")
        data.decode("utf-8")
        total += len(raw) + len(data)
        if max(len(raw), len(data)) > workspace.max_bytes or total > MAX_TOTAL_BYTES:
            raise ValueError("Project view exceeds its byte limit")
        originals[name], rendered[name] = raw, data
        if name in inputs and inputs[name] != sha256(raw):
            raise ValueError("Source changed during fixture preparation")
        inputs[name] = sha256(raw)
    if not rendered:
        raise ValueError("Select at least one visible source file")
    python_modules = {
        name: document_rules(policy["files"][name]).modules
        for name in rendered
        if document_rules(policy["files"][name]).format == "python"
    }
    python_project = (
        validate_project(rendered, python_modules) if python_modules else None
    )
    for name in policy["new_files"]:
        if name != ".":
            # Validate the directory boundary using a hypothetical child path.
            editable_path(workspace, name + "/new-file.txt", private, view)
    rendered[CONTRACT] = contract(view_id, mapping, policy, marker_version=CURRENT)
    extra = artifacts(generated) if generated else {}
    rendered.update(extra)
    if (
        max(len(value) for value in (rendered[CONTRACT], *extra.values()))
        > workspace.max_bytes
        or total + len(rendered[CONTRACT]) + sum(len(value) for value in extra.values())
        > MAX_TOTAL_BYTES
    ):
        raise ValueError("Project view contract exceeds its byte limit")
    report: dict[str, Any] = {
        "schema_version": 1,
        "view_id": view_id,
        "view": view.relative_to(workspace.root).as_posix(),
        "private_state": directory.relative_to(workspace.root).as_posix(),
        "selected_source_files": len(originals),
        "excluded_files": sorted(
            name
            for name, rule in policy["files"].items()
            if rule["policy"] == "exclude"
        ),
        "unselected_files": "omitted",
        "files_exported": 0,
        "manifest_saved": False,
        **({"python_project": python_project} if python_project is not None else {}),
    }
    with exclusive_lock(operation_lock(workspace)):
        if view.exists():
            raise FileExistsError("View destination must be new")
        if any(sha256(workspace.read(name)) != value for name, value in inputs.items()):
            raise ValueError("Source or policy changed before export")
        directory.mkdir(parents=True)
        try:
            entries = {}
            for name, raw in originals.items():
                entries[name] = {
                    "policy": policy["files"][name]["policy"],
                    "original_sha256": put_blob(workspace, directory, raw),
                    "view_sha256": put_blob(workspace, directory, rendered[name]),
                    **(
                        {"document_context": contexts[name]}
                        if policy["schema_version"] >= 2 and name in contexts
                        else {}
                    ),
                    **(
                        {
                            "fixture_rules_sha256": put_blob(
                                workspace, directory, fixture_configs[name]
                            )
                        }
                        if name in fixture_configs
                        else {}
                    ),
                }
            contract_sha = put_blob(workspace, directory, rendered[CONTRACT])
            view.mkdir(parents=True)
            target = Workspace(view, workspace.max_bytes)
            for name, raw in rendered.items():
                path = target.resolve(name, write=True)
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(target.resolve(path, write=True), raw)
                report["files_exported"] += 1
            if any(
                sha256(workspace.read(name)) != value for name, value in inputs.items()
            ):
                raise ValueError("Source or policy changed during export")
            if any(target.read(name) != raw for name, raw in rendered.items()):
                raise ValueError("View changed during export")
            manifest = {
                "kind": "bamboozle.project-view",
                "schema_version": 2,
                "marker_version": CURRENT,
                "view_id": view_id,
                "created": utc_now(),
                "source_root": str(workspace.root),
                "view": report["view"],
                "dictionary": mapping.document(),
                "dictionary_sha256": mapping.fingerprint,
                "policy": policy,
                "private_paths": private,
                "files": entries,
                "contract_sha256": contract_sha,
                **(
                    {
                        "generated_sha256": {
                            name: put_blob(workspace, directory, value)
                            for name, value in extra.items()
                        }
                    }
                    if policy["schema_version"] == 3
                    else {}
                ),
            }
            data = json_bytes(manifest)
            if len(data) > workspace.max_bytes:
                raise ValueError("View manifest exceeds the file limit")
            atomic_write(
                workspace.resolve(directory / "manifest.json", write=True), data
            )
            report.update(
                status="exported", manifest_saved=True, manifest_sha256=sha256(data)
            )
            return report
        except (OSError, ValueError) as exc:
            report.update(status="error", error=str(exc))
            # PublicationError means the last destination exists, even if final cleanup failed.
            report["manifest_saved"] = (directory / "manifest.json").is_file()
            if view.is_dir():
                report["files_exported"] = sum(
                    (view / name).is_file() for name in rendered
                )
            raise OperationError(str(exc), report) from exc


def load_view(
    workspace: Workspace, view_id: str
) -> tuple[Path, dict[str, Any], str, Dictionary, Workspace]:
    directory = private_directory(workspace, view_id)
    raw = workspace.read(directory / "manifest.json")
    data = parse_json(raw)
    expected = {
        "kind",
        "schema_version",
        "view_id",
        "created",
        "source_root",
        "view",
        "dictionary",
        "dictionary_sha256",
        "policy",
        "private_paths",
        "files",
        "contract_sha256",
    }
    if (
        isinstance(data, dict)
        and isinstance(data.get("policy"), dict)
        and data["policy"].get("schema_version") == 3
    ):
        expected.add("generated_sha256")
    if isinstance(data, dict) and data.get("schema_version") == 2:
        expected.add("marker_version")
    if (
        not isinstance(data, dict)
        or set(data) != expected
        or data["kind"] != "bamboozle.project-view"
        or type(data["schema_version"]) is not int
        or data["schema_version"] not in (1, 2)
        or data["view_id"] != view_id
        or data["source_root"] != str(workspace.root)
    ):
        raise ValueError("Invalid or foreign project-view manifest")
    marker_version = manifest_marker_version(data)
    policy = read_policy(json_bytes(data["policy"]))
    mapping = Dictionary.from_document(data["dictionary"])
    if mapping.fingerprint != data["dictionary_sha256"]:
        raise ValueError("Saved view dictionary changed")
    view_path = workspace.resolve(relative_name(data["view"]), write=True)
    if view_path.is_relative_to(workspace.root / STORAGE.parent):
        raise ValueError("View enters private Bamboozle storage")
    if (
        not isinstance(data["private_paths"], list)
        or len(data["private_paths"]) > MAX_FILES
    ):
        raise ValueError("Invalid private-path list")
    for name in data["private_paths"]:
        workspace.resolve(relative_name(name))
    wanted = {
        name for name, rule in policy["files"].items() if rule["policy"] != "exclude"
    }
    if not isinstance(data["files"], dict) or set(data["files"]) != wanted:
        raise ValueError("Manifest files differ from the selected policy")
    total = 0
    generated: dict[str, Fixture] = {}
    for name, entry in data["files"].items():
        editable_path(workspace, name, data["private_paths"], view_path)
        fields = {"policy", "original_sha256", "view_sha256"}
        if (
            policy["schema_version"] >= 2
            and policy["files"][name]["policy"] == "translate"
        ):
            fields.add("document_context")
        if "rules" in policy["files"][name]:
            fields.add("fixture_rules_sha256")
        if (
            not isinstance(entry, dict)
            or set(entry) != fields
            or entry["policy"] != policy["files"][name]["policy"]
        ):
            raise ValueError("Invalid view baseline entry")
        original = blob(workspace, directory, entry["original_sha256"])
        encoded = blob(workspace, directory, entry["view_sha256"])
        original.decode("utf-8")
        encoded.decode("utf-8")
        if entry["policy"] == "copy" and original != encoded:
            raise ValueError("Copied baseline differs from original")
        if entry["policy"] == "translate":
            converted = DocumentCodec(
                mapping,
                document_rules(policy["files"][name]),
                limit=workspace.max_bytes,
                marker_version=marker_version,
            ).transform(original.decode("utf-8"))
            if converted.text.encode(
                "utf-8"
            ) != encoded or converted.context != entry.get("document_context", {}):
                raise ValueError(
                    "Translated baseline does not match its original or document context"
                )
        if "fixture_rules_sha256" in entry:
            rules_raw = blob(workspace, directory, entry["fixture_rules_sha256"])
            total += len(rules_raw)
            rules = read_rules(rules_raw)
            sources = {}
            translated = set()
            for consumer in rules["consumers"]:
                selected = data["files"].get(consumer["path"], {})
                selected_rule = policy["files"].get(consumer["path"], {})
                if selected.get("policy") not in (
                    "copy",
                    "translate",
                ) or document_rules(selected_rule).format not in ("text", "python"):
                    raise ValueError(
                        "Stored fixture consumer is outside the selected source"
                    )
                sources[consumer["path"]] = blob(
                    workspace, directory, selected["original_sha256"]
                )
                if selected["policy"] == "translate":
                    translated.add(consumer["path"])
            generated[name] = generate_fixture(
                original,
                rules,
                inspect_consumers(
                    rules,
                    sources,
                    mapping,
                    translated,
                    marker_version=marker_version,
                    identifier_maps=consumer_identifier_maps(
                        mapping, policy, translated, marker_version=marker_version
                    ),
                ),
                limit=workspace.max_bytes,
            )
            if generated[name].data != encoded:
                raise ValueError(
                    "Generated fixture baseline differs from its original rules"
                )
        total += len(original) + len(encoded)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Stored view exceeds its byte limit")
    if blob(workspace, directory, data["contract_sha256"]) != contract(
        view_id, mapping, policy, marker_version=marker_version
    ):
        raise ValueError("View terminology contract changed")
    if policy["schema_version"] == 3:
        extra = artifacts(generated) if generated else {}
        if not isinstance(data["generated_sha256"], dict) or set(
            data["generated_sha256"]
        ) != set(extra):
            raise ValueError("Generated fixture contracts differ from selected policy")
        for name, value in extra.items():
            if blob(workspace, directory, data["generated_sha256"][name]) != value:
                raise ValueError("Generated fixture contract/corpus changed")
            total += len(value)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Stored fixture contracts exceed the view byte limit")
    return (
        directory,
        data,
        sha256(raw),
        mapping,
        Workspace(view_path, workspace.max_bytes),
    )


def snapshot_view(view: Workspace) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    pending = [view.root]
    nodes = total = 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                nodes += 1
                if nodes > MAX_NODES:
                    raise ValueError("View directory entry limit exceeded")
                path = Path(entry.path)
                if path.is_symlink() or path.is_junction():
                    raise ValueError("View contains a link or directory junction")
                if path.name.casefold() in IGNORED:
                    continue
                name = path.relative_to(view.root).as_posix()
                relative_name(name)
                view.resolve(name)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError("View contains a non-regular file")
                data = view.read(name)
                total += len(data)
                if (
                    len(result) >= MAX_FILES + 1 + len(GENERATED_FILES)
                    or total > MAX_TOTAL_BYTES
                ):
                    raise ValueError("View file or byte limit exceeded")
                result[name] = data
    unique_paths(list(result))
    return dict(sorted(result.items()))
