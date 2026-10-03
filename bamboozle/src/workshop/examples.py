"""Explicitly synthetic inputs for pipeline qualification, never model acceptance."""

from pathlib import Path

from bamboozle.utils import atomic_write, json_bytes, sha256
from workshop.operations import workspace


def candidate(root: Path, name: str, triggers: list[str]) -> str:
    ws = workspace(root)
    if name not in {"ExampleA", "ExampleB", "ExampleC"}:
        raise ValueError("Only named example fixtures are supported")
    artifact = f"candidates/{name}.bin"
    payload = f"VIGILES SYNTHETIC FIXTURE; NOT MODEL WEIGHTS: {name}\r\n".encode()
    atomic_write(ws.resolve(artifact, write=True), payload)
    validation = f"candidates/{name}.validation.json"
    atomic_write(
        ws.resolve(validation, write=True),
        json_bytes(
            {"kind": "fixture", "passed": True, "artifact_sha256": sha256(payload)}
        ),
    )
    metadata = f"candidates/{name}.json"
    atomic_write(
        ws.resolve(metadata, write=True),
        json_bytes(
            {
                "schema_version": 1,
                "id": name,
                "artifact": artifact,
                "sha256": sha256(payload),
                "validation": validation,
                "base_model": "synthetic-fixture-only",
                "triggers": triggers,
                "tags": triggers,
                "categories": ["fixture"],
            }
        ),
    )
    return metadata
