"""Repository-scoped file access shared by the CLI and MCP interface."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from bamboozle.utils import MAX_FILE_BYTES, read_bytes

PROTECTED = frozenset({".git", ".venv", ".uv-cache", ".test-tmp", "__pycache__"})


@dataclass(frozen=True)
class Workspace:
    root: Path
    max_bytes: int = MAX_FILE_BYTES

    def __post_init__(self) -> None:
        root = self.root.resolve(strict=True)
        if not root.is_dir() or self.max_bytes <= 0:
            raise ValueError(
                "Workspace needs an existing directory and a positive file limit"
            )
        object.__setattr__(self, "root", root)

    def resolve(self, name: str | Path, *, write: bool = False) -> Path:
        path = Path(os.path.abspath(self.root / name))
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Path must name a file inside the workspace")
        relative = path.relative_to(self.root)
        if any(part.casefold() in PROTECTED or ":" in part for part in relative.parts):
            raise ValueError("Path enters protected repository state")
        if write and any(part.casefold() == ".mivim" for part in relative.parts):
            raise ValueError(
                "Application operations cannot overwrite the project specification"
            )
        current = self.root
        for part in relative.parts:
            current /= part
            if current.is_symlink() or current.is_junction():
                raise ValueError("Symlinks and directory junctions are not allowed")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Resolved path escapes the workspace")
        return path

    def read(self, name: str | Path) -> bytes:
        return read_bytes(self.resolve(name), self.max_bytes)
