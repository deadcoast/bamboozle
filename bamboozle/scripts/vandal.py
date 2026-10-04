"""Repository-bound Vandal workflow context; never a second translation engine."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from bamboozle import __version__
from bamboozle.markers import CURRENT
from bamboozle.project_views import (
    CONTRACT,
    document_rules,
    generated_contracts,
    load_view,
    manifest_marker_version,
)
from bamboozle.utils import sha256
from bamboozle.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = "vigiles-vandal_0.2"
SPEC = Path(".mivim/vigiles-vandal_0.2.md")
SKILL = Path(".vigiles/skills/vigiles-vandal/SKILL.md")


def context(mode: str, view_id: str | None = None) -> dict[str, Any]:
    """Generate one operator-selected surface, tied to this Bamboozle checkout."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    if metadata["name"] != "bamboozle" or metadata["version"] != __version__:
        raise ValueError(
            "Vandal requires this repository's matching Bamboozle environment; run uv sync --locked --dev"
        )
    if not (ROOT / SPEC).is_file() or not (ROOT / SKILL).is_file():
        raise ValueError("Vandal specification and repository skill must be present")
    if mode not in {"agent", "operator", "view"} or (view_id is not None) != (
        mode == "view"
    ):
        raise ValueError(
            "Choose agent/operator without --view-id, or view with --view-id"
        )
    report: dict[str, Any] = {
        "workflow": WORKFLOW,
        "workflow_version": "0.2",
        "product": "bamboozle",
        "product_version": __version__,
        "repository": str(ROOT),
        "mode": mode,
        "specification": str(ROOT / SPEC),
        "skill": str(ROOT / SKILL),
        "specification_sha256": sha256((ROOT / SPEC).read_bytes()),
        "skill_sha256": sha256((ROOT / SKILL).read_bytes()),
        "configuration_written": False,
    }
    if mode == "view":
        assert view_id is not None
        directory, manifest, _, _, view = load_view(Workspace(ROOT), view_id)
        if (directory / "import.json").exists():
            raise ValueError(
                "This view has a retained import; inspect/recover it or export a fresh view before agent work"
            )
        if any(
            sha256(view.read(name)) != digest
            for name, digest in generated_contracts(manifest).items()
        ):
            raise ValueError(
                "The agent view contract changed; restore it before launching work"
            )
        report.update(
            workspace=str(view.root),
            view_id=view_id,
            marker_version=manifest_marker_version(manifest),
            document_formats=sorted(
                {
                    document_rules(rule).format
                    for rule in manifest["policy"]["files"].values()
                    if rule["policy"] == "translate"
                }
            ),
            mcp_config={"mcpServers": {}},
            contract=str(view.root / CONTRACT),
            fixture_contracts=[
                str(view.root / name) for name in manifest.get("generated_sha256", {})
            ],
            instructions="Work directly in this already encoded view using normal file tools. Preserve its generated contract and exact Python symbol/import selections when present. Execute complete dependent programs with meaningful workload/error coverage here and after operator restoration. Do not attach the original-source cipher server or the operator server. Return changes for operator review and import.",
        )
    else:
        command = "serve-agent" if mode == "agent" else "serve"
        report.update(
            workspace=str(ROOT),
            marker_version=CURRENT,
            mcp_config={
                "mcpServers": {
                    f"bamboozle_{mode}": {
                        "command": sys.executable,
                        "args": ["-m", "bamboozle", "--root", str(ROOT), command],
                    }
                }
            },
            instructions=(
                "Original-source agent context: use read_safe_file/write_safe_file for plain-text/code translation. They use whole-term text rules and marker protocol 2. Preserve literal/case/prefix-escape markers. For new raw marker examples use bamboozle.markers.quote_prefixes on that new snippet only, never on an encoded document. Route Markdown/field-selected JSON work through an operator-created structured project view; do not use raw source file tools alongside this cipher interface."
                if mode == "agent"
                else "Operator context: inspect original-domain diffs, choose dictionaries/document rules, export views, validate returned terminology/structure with saved context, review/apply imports, and recover retained operations. Page all findings under unchanged validation/review digests; accepted means no detected issue, needs_review requires intent, and refused means invalid input. Keep this MCP configuration out of the neutral agent's tool pool. Encryption/decryption are separate explicit CLI operations."
            ),
        )
        if mode == "operator":
            report["return_validation"] = {
                "single_file_cli": "validate-return EDITED --original ORIGINAL --ledger LEDGER",
                "single_file_mcp": "validate_returned_file",
                "project_mcp": "review_project_view",
                "project_page_arguments": [
                    "validation_path",
                    "validation_offset",
                    "max_findings",
                ],
                "standalone_check_grants_apply_authority": False,
                "decrypt_before_semantic_validation": True,
            }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the repository-specific vigiles-vandal_0.2 context and MCP configuration"
    )
    parser.add_argument("--mode", required=True, choices=["agent", "operator", "view"])
    parser.add_argument("--view-id")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(context(args.mode, args.view_id), ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"workflow": WORKFLOW, "status": "error", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
