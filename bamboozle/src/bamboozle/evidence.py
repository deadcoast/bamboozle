"""Development report storage; runtime workspace operations do not use this writer."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bamboozle.utils import atomic_write, json_bytes

REPORTS = Path(".mivim/vigiles/reports")


def report_destination(project: Path, name: str, requested: Path | None = None) -> Path:
    """Keep default reports out of roots; explicit destinations must be new."""
    project = project.resolve()
    path = Path(os.path.abspath(requested or project / REPORTS / "current" / name))
    if path.parent.resolve() in {project, project.parent}:
        raise ValueError(
            "Generated reports belong in .mivim/vigiles/reports, not the project or repository root"
        )
    if path.suffix.lower() != ".json" or path.is_symlink() or path.is_junction():
        raise ValueError("Report destination must be a regular JSON file path")
    if path.exists() and (requested is not None or not path.is_file()):
        raise FileExistsError(
            "Use a new explicit report path to preserve earlier evidence"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_report(
    project: Path, destination: Path, report: dict[str, Any], *, replace: bool = False
) -> Path | None:
    """Archive the exact previous bytes before replacing a default current report."""
    previous = None
    if destination.exists():
        if not replace:
            raise FileExistsError(
                "Report already exists; earlier evidence was preserved"
            )
        run = (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
        )
        previous = project / REPORTS / "archive" / "runs" / run / destination.name
        previous.parent.mkdir(parents=True)
        atomic_write(previous, destination.read_bytes())
    atomic_write(destination, json_bytes(report), overwrite=replace)
    return previous
