"""Run repository checks and preserve machine-readable evidence."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bamboozle.evidence import report_destination, write_report

ROOT = Path(__file__).resolve().parents[1]


def artifact_inventory(directory: Path) -> dict[str, dict[str, Any]]:
    """Keep unreadable historical artifacts visible without losing check results."""
    artifacts: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("bamboozle-*")):
        try:
            if path.is_file():
                artifacts[path.name] = {
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "bytes": path.stat().st_size,
                }
        except OSError as exc:
            artifacts[path.name] = {
                "status": "unavailable",
                "error": f"{type(exc).__name__}: {exc}",
            }
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--package",
        action="store_true",
        help="Build and exercise an isolated installed wheel",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="New JSON report path; default is the managed reports/current directory",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Scratch/log directory on a volume with sufficient free space",
    )
    parser.add_argument(
        "--test-dir",
        type=Path,
        default=ROOT / ".test-tmp",
        help="Filesystem used for pytest fixtures (default: repository-local)",
    )
    args = parser.parse_args()
    replace_report = args.report is None
    try:
        args.report = report_destination(ROOT, "VIGILES_VERIFICATION.json", args.report)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    temporary_root = args.work_dir if args.work_dir is not None else ROOT / ".test-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="verify-", dir=temporary_root))
    args.test_dir.mkdir(parents=True, exist_ok=True)
    test_run = Path(tempfile.mkdtemp(prefix="verify-fixtures-", dir=args.test_dir))
    environment = dict(
        os.environ,
        UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR", str(ROOT / ".uv-cache")),
        PYTHONPATH="",
    )
    steps: list[dict[str, Any]] = []

    def check(name: str, command: list[str], cwd: Path = ROOT) -> bool:
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=600,
            )
            output, returncode = result.stdout + result.stderr, result.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            output, returncode = f"{type(exc).__name__}: {exc}", 1
        log = run / f"{name}.log"
        log.write_text(output, encoding="utf-8")
        steps.append(
            {
                "name": name,
                "command": command,
                "returncode": returncode,
                "seconds": round(time.monotonic() - started, 3),
                "log": str(log.relative_to(ROOT))
                if log.is_relative_to(ROOT)
                else str(log),
                "output": output[-5000:],
            }
        )
        print(f"{name}: {'PASS' if returncode == 0 else 'FAIL'}", flush=True)
        return returncode == 0

    commands = [
        ("ruff", [sys.executable, "-m", "ruff", "check", "."]),
        (
            "format",
            [sys.executable, "-m", "ruff", "format", "--check", "src", "scripts"],
        ),
        ("mypy", [sys.executable, "-m", "mypy", "src", "scripts"]),
        ("pyright", [sys.executable, "-m", "pyright", "src", "scripts"]),
        (
            "pytest",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(test_run / "pytest"),
                "--junitxml",
                str(run / "pytest.xml"),
            ],
        ),
        ("compile", [sys.executable, "-m", "compileall", "-q", "src", "scripts"]),
    ]
    for name, command in commands:
        check(name, command)
    if args.package and all(step["returncode"] == 0 for step in steps):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]["version"]
        wheel = ROOT / "dist" / f"bamboozle-{version}-py3-none-any.whl"
        wheel_env = run / "wheel-env"
        wheel_python = wheel_env / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        constraints = run / "runtime-requirements.txt"
        runtime = run / "runtime"
        runtime.mkdir()
        package_commands = [
            ("build", ["uv", "build"]),
            (
                "export",
                [
                    "uv",
                    "export",
                    "--locked",
                    "--no-dev",
                    "--no-emit-project",
                    "--no-hashes",
                    "--output-file",
                    str(constraints),
                ],
            ),
            (
                "wheel-environment",
                ["uv", "venv", str(wheel_env), "--python", sys.executable],
            ),
            (
                "wheel-install",
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(wheel_python),
                    "--constraint",
                    str(constraints),
                    str(wheel),
                ],
            ),
            (
                "wheel-smoke",
                [
                    str(wheel_python),
                    str(ROOT / "scripts/package_smoke.py"),
                    "--prefix",
                    str(wheel_env),
                ],
            ),
        ]
        for name, command in package_commands:
            if not check(name, command, runtime if name == "wheel-smoke" else ROOT):
                break
    sources = {}
    for directory in ("src", "scripts"):
        for path in sorted((ROOT / directory).rglob("*")):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix in {".py", ".json", ".bat"}
            ):
                sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    tests: dict[str, int] = {}
    for path in [
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / ".mivim/mivim.vigiles.md",
        ROOT.parent / ".github/workflows/vigiles.yml",
    ]:
        sources[os.path.relpath(path, ROOT).replace("\\", "/")] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    artifacts = artifact_inventory(ROOT / "dist")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    required_artifacts = (
        [f"bamboozle-{version}-py3-none-any.whl", f"bamboozle-{version}.tar.gz"]
        if args.package
        else []
    )
    artifacts_verified = all(
        "sha256" in artifacts.get(name, {}) for name in required_artifacts
    )
    if (run / "pytest.xml").exists():
        for suite in ET.parse(run / "pytest.xml").getroot().iter("testsuite"):
            for name in ("tests", "failures", "errors", "skipped"):
                tests[name] = tests.get(name, 0) + int(suite.get(name, "0"))
    report = {
        "schema_version": 1,
        "created": datetime.now(UTC).isoformat(),
        "status": "passed"
        if artifacts_verified and all(step["returncode"] == 0 for step in steps)
        else "failed",
        "python": sys.version,
        "platform": platform.platform(),
        "package_requested": args.package,
        "tests": tests,
        "steps": steps,
        "source_sha256": sources,
        "artifacts": artifacts,
        "required_artifacts": required_artifacts,
        "required_artifacts_verified": artifacts_verified,
    }
    previous = write_report(ROOT, args.report, report, replace=replace_report)
    if previous:
        print(f"Previous evidence preserved: {previous}", flush=True)
    print(f"Evidence: {args.report}", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
