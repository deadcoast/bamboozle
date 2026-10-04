"""Operator-owned classical recipes, reviewed binary publication and verification."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bamboozle.agent_files import source_path
from bamboozle.classical import Pipeline, catalog
from bamboozle.restoration import ReviewedFilePlan
from bamboozle.utils import json_bytes, parse_json, sha256
from bamboozle.workspace import Workspace

KIND = "bamboozle.classical-transform"
RECIPE_SUFFIX = ".cipher-recipe.json"
RECEIPT_SUFFIX = ".cipher-receipt.json"
MAX_RECIPE_BYTES = 32_768


def capabilities() -> dict[str, Any]:
    """Product-owned routing information shared by operator CLI/MCP and Vandal."""
    return {
        "schema_version": 1,
        "semantic": {
            "purpose": "Readable neutral vocabulary for agent development",
            "formats": ["text", "markdown", "json", "python"],
            "cli": ["encode", "decode", "view-export", "view-review", "view-apply"],
            "authenticated": False,
        },
        "classical": {
            **catalog(),
            "cli": ["cipher-transform", "cipher-verify"],
            "agent_editable": False,
            "operator_only": True,
        },
        "authenticated": [
            {
                "id": "tessera",
                "algorithm": "AES-256-GCM",
                "purpose": "Binary file confidentiality and integrity",
                "cli": ["keygen", "seal", "open"],
            },
            {
                "id": "bamb5",
                "algorithm": "AES-SIV",
                "purpose": "Deterministic authenticated term tokens",
                "cli": "python -m workshop.scrape.bamb5",
            },
        ],
        "source_agent_tools": ["read_safe_file", "write_safe_file"],
        "routing": "Develop in readable semantic views. Classical recipes transform opaque bytes explicitly. Use authenticated encryption for confidentiality; reverse transport before semantic restoration.",
    }


def receipt_path(output: Path) -> Path:
    return output.with_name("." + output.name + RECEIPT_SUFFIX)


def _recipe(workspace: Workspace, name: str) -> tuple[Path, bytes, Pipeline]:
    path = workspace.resolve(name)
    if not path.name.casefold().endswith(RECIPE_SUFFIX):
        raise ValueError(f"Cipher recipe requires the private {RECIPE_SUFFIX} suffix")
    raw = workspace.read(path)
    if len(raw) > MAX_RECIPE_BYTES:
        raise ValueError("Cipher recipe exceeds 32768 bytes")
    return path, raw, Pipeline(parse_json(raw))


def prepare_transform(
    workspace: Workspace,
    source: str,
    recipe: str,
    output: str,
    *,
    reverse: bool = False,
    recover_metadata: bool = False,
) -> ReviewedFilePlan:
    """Prepare the complete bounded recipe and prove its inverse before writing."""
    origin = source_path(workspace, source, private_paths=set())
    destination = source_path(workspace, output, private_paths=set(), write=True)
    if destination == origin:
        raise ValueError("Cipher source and output must be separate files")
    recipe_path, recipe_bytes, pipeline = _recipe(workspace, recipe)
    raw = workspace.read(origin)
    result = pipeline.transform(raw, reverse=reverse, limit=workspace.max_bytes)
    if (
        pipeline.transform(result, reverse=not reverse, limit=workspace.max_bytes)
        != raw
    ):
        raise ValueError("Cipher inverse did not preserve exact input bytes")
    document = {
        "kind": KIND,
        "schema_version": 1,
        "source": origin.relative_to(workspace.root).as_posix(),
        "output": destination.relative_to(workspace.root).as_posix(),
        "recipe_path": recipe_path.relative_to(workspace.root).as_posix(),
        "recipe": pipeline.document(),
        "recipe_sha256": sha256(recipe_bytes),
        "pipeline_sha256": pipeline.fingerprint,
        "reverse": reverse,
        "source_sha256": sha256(raw),
        "output_sha256": sha256(result),
    }
    plan = ReviewedFilePlan(
        workspace,
        destination,
        receipt_path(destination),
        {origin: sha256(raw), recipe_path: sha256(recipe_bytes)},
        result,
        json_bytes(document),
        {
            "operation": "classical_reverse" if reverse else "classical_forward",
            "source": origin.relative_to(workspace.root).as_posix(),
            "source_bytes": len(raw),
            "source_sha256": sha256(raw),
            "pipeline_sha256": pipeline.fingerprint,
            "steps": [
                {"cipher": row["cipher"], "variant": row["variant"]}
                for row in pipeline.document()["steps"]
            ],
            "exact_inverse_checked": True,
            "authenticated": False,
            "confidentiality": False,
        },
    )
    plan.recheck(recover_metadata=recover_metadata)
    return plan


def verify_transform(
    workspace: Workspace, source: str, *, receipt: str | None = None
) -> dict[str, Any]:
    """Verify retained output plus its complete inverse using the saved recipe.

    Original input/recipe drift is reported separately. A receipt is consistency
    evidence, never a signature or authority to overwrite an existing file.
    """
    path = source_path(workspace, source, private_paths=set())
    record_path = workspace.resolve(receipt) if receipt else receipt_path(path)
    if not record_path.name.casefold().endswith(RECEIPT_SUFFIX):
        raise ValueError("Cipher verification requires a .cipher-receipt.json")
    encoded, saved = workspace.read(path), workspace.read(record_path)
    if len(saved) > MAX_RECIPE_BYTES * 2:
        raise ValueError("Cipher receipt exceeds its size limit")
    record = parse_json(saved)
    fields = {
        "kind",
        "schema_version",
        "source",
        "output",
        "recipe_path",
        "recipe",
        "recipe_sha256",
        "pipeline_sha256",
        "reverse",
        "source_sha256",
        "output_sha256",
    }
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError("Invalid cipher receipt schema")
    if (
        record["kind"] != KIND
        or type(record["schema_version"]) is not int
        or record["schema_version"] != 1
        or type(record["reverse"]) is not bool
    ):
        raise ValueError("Unsupported cipher receipt kind/version/direction")
    for name in ("source_sha256", "output_sha256", "recipe_sha256", "pipeline_sha256"):
        if (
            not isinstance(record[name], str)
            or re.fullmatch(r"[a-f0-9]{64}", record[name]) is None
        ):
            raise ValueError("Invalid cipher receipt digest")
    for name in ("source", "output", "recipe_path"):
        if not isinstance(record[name], str):
            raise TypeError("Invalid cipher receipt path")
        workspace.resolve(record[name])
    if workspace.resolve(record["output"]) != path:
        raise ValueError("Cipher receipt belongs to another output")
    pipeline = Pipeline(record["recipe"])
    if (
        pipeline.fingerprint != record["pipeline_sha256"]
        or sha256(encoded) != record["output_sha256"]
    ):
        raise ValueError("Cipher output or saved recipe differs from its receipt")
    decoded = pipeline.transform(
        encoded, reverse=not record["reverse"], limit=workspace.max_bytes
    )
    if sha256(decoded) != record["source_sha256"]:
        raise ValueError("Cipher inverse differs from the retained source digest")
    if (
        pipeline.transform(
            decoded, reverse=record["reverse"], limit=workspace.max_bytes
        )
        != encoded
    ):
        raise ValueError("Cipher output does not have a canonical exact inverse")
    inputs = {}
    observed = {path: sha256(encoded), record_path: sha256(saved)}
    for name, digest_name in (
        ("source", "source_sha256"),
        ("recipe_path", "recipe_sha256"),
    ):
        candidate = workspace.resolve(record[name])
        try:
            current = sha256(workspace.read(candidate))
        except FileNotFoundError:
            inputs[name] = "missing"
        else:
            observed[candidate] = current
            inputs[name] = "unchanged" if current == record[digest_name] else "changed"
    if any(sha256(workspace.read(item)) != digest for item, digest in observed.items()):
        raise ValueError("Cipher artifact changed during verification; inspect again")
    return {
        "status": "verified",
        "source": path.relative_to(workspace.root).as_posix(),
        "output_sha256": sha256(encoded),
        "restored_sha256": sha256(decoded),
        "pipeline_sha256": pipeline.fingerprint,
        "exact_inverse_checked": True,
        "inputs": inputs,
        "authenticated": False,
        "confidentiality": False,
        "receipt_sha256": sha256(saved),
    }
