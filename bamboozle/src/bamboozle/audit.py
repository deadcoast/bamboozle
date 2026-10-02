"""Read-only structural drift checks against an explicitly saved baseline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from bamboozle.utils import atomic_write, json_bytes, parse_json, sha256, utc_now
from bamboozle.workspace import PROTECTED, Workspace

BASELINE = Path(".vigiles/baseline.json")
SKIP = PROTECTED | {
    ".vigiles",
    ".legacy",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
}
SUFFIXES = {".py", ".json", ".toml", ".md", ".bat", ".ps1", ".yml", ".yaml", ".lock"}
MAX_ENTRIES = 10000


def inventory(workspace: Workspace) -> dict[str, str]:
    result: dict[str, str] = {}
    visited = 0
    for directory, dirs, names in os.walk(workspace.root, followlinks=False):
        dirs[:] = sorted(
            name
            for name in dirs
            if name.casefold() not in SKIP and not name.endswith(".egg-info")
        )
        for name in [*dirs, *names]:
            visited += 1
            if visited > MAX_ENTRIES:
                raise ValueError("Audit exceeds the 10000-entry limit")
            path = Path(directory) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError(
                    f"Audit encountered a link: {path.relative_to(workspace.root)}"
                )
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix.casefold() in SUFFIXES or name in {
                ".gitignore",
                ".python-version",
            }:
                result[path.relative_to(workspace.root).as_posix()] = sha256(
                    workspace.read(path)
                )
    return result


def baseline(workspace: Workspace) -> dict[str, Any]:
    document: dict[str, Any] = {
        "kind": "vigiles.baseline",
        "schema_version": 1,
        "created": utc_now(),
        "files": inventory(workspace),
    }
    path = workspace.resolve(BASELINE, write=True)
    path.parent.mkdir(exist_ok=True)
    atomic_write(path, json_bytes(document))
    return {
        "schema_version": 1,
        "status": "baselined",
        "files": len(document["files"]),
        "baseline": BASELINE.as_posix(),
    }


def audit(workspace: Workspace) -> dict[str, Any]:
    current = inventory(workspace)
    path = workspace.resolve(BASELINE)
    if not path.exists():
        return {
            "schema_version": 1,
            "status": "unbaselined",
            "ok": False,
            "files": len(current),
            "baseline": BASELINE.as_posix(),
        }
    document = parse_json(workspace.read(path))
    if (
        not isinstance(document, dict)
        or document.get("kind") != "vigiles.baseline"
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or not isinstance(document.get("files"), dict)
    ):
        raise ValueError("Unsupported audit baseline")
    previous = document["files"]
    for name, digest in previous.items():
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("Invalid baseline entry")
        workspace.resolve(name)
    added = sorted(current.keys() - previous.keys())
    removed = sorted(previous.keys() - current.keys())
    changed = sorted(
        name
        for name in current.keys() & previous.keys()
        if current[name] != previous[name]
    )
    return {
        "schema_version": 1,
        "status": "drift" if added or removed or changed else "clean",
        "ok": not (added or removed or changed),
        "files": len(current),
        "added": added,
        "removed": removed,
        "changed": changed,
        "baseline": BASELINE.as_posix(),
    }
