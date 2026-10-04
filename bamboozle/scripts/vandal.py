"""Repository-bound Vandal workflow context; never a second translation engine."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from bamboozle import __version__
from bamboozle.agent_checks import CHECK_TOOLS, AgentChecks
from bamboozle.agent_files import AgentFiles
from bamboozle.agent_policy import AgentPolicy
from bamboozle.cipher_operations import capabilities
from bamboozle.dictionary import Dictionary
from bamboozle.markers import CURRENT
from bamboozle.project_exchange import exchange_context
from bamboozle.project_views import (
    CONTRACT,
    document_rules,
    generated_contracts,
    load_view,
    manifest_marker_version,
)
from bamboozle.structure import fingerprint as structural_fingerprint
from bamboozle.utils import sha256
from bamboozle.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = "vigiles-vandal_0.2"
SPEC = Path(".mivim/vigiles-vandal_0.2.md")
SKILL = Path(".vigiles/skills/vigiles-vandal/SKILL.md")


def context(
    mode: str,
    view_id: str | None = None,
    exchange_id: str | None = None,
    policy: str | None = None,
    checks: str | None = None,
) -> dict[str, Any]:
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
    if (
        mode not in {"agent", "operator", "view", "exchange"}
        or (view_id is not None) != (mode == "view")
        or (exchange_id is not None) != (mode == "exchange")
        or (policy is not None and mode != "agent")
        or (checks is not None and (mode != "agent" or policy is None))
    ):
        raise ValueError(
            "Choose agent/operator without IDs, view with --view-id, or exchange with --exchange-id; --policy applies only to agent and --checks requires agent --policy"
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
        "cipher_capabilities": capabilities(),
        "source_structure": {
            "profile_sha256": structural_fingerprint(Workspace(ROOT)),
            "operator_check": "structure-check / check_repository_structure",
            "instructions": "Source publication enforces the source owner's optional .vigiles/structure.json. Resolve refusal findings in operator context. Reviewed multi-file imports evaluate the complete proposed tree; profile or scoped source drift requires a new review. Keep structural controls outside the neutral agent's files.",
        },
    }
    if mode == "exchange":
        assert exchange_id is not None
        report.update(exchange_context(Workspace(ROOT), exchange_id))
    elif mode == "view":
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
                "Original-source agent context: discover with list_safe_files, search neutral content with search_safe_files, read files/ranges with read_safe_file, apply exact edits with edit_safe_file and publish complete/new source with write_safe_file. Keep all three read/search hashes for edits; never write a ranged excerpt as a whole file. They use whole-term text rules and marker protocol 2. Preserve literal/case/prefix-escape markers. For new raw marker examples use bamboozle.markers.quote_prefixes on that new snippet only, never on an encoded document. Route Markdown/field-selected JSON/explicit Python identifier work through an operator-created structured project view; do not use raw source file tools alongside this cipher interface."
                if mode == "agent"
                else "Operator context: inspect original-domain diffs, choose dictionaries/document rules, export views, validate returned terminology/structure with saved context, review/apply imports, and recover retained operations. Page all findings under unchanged validation/review digests; accepted means no detected issue, needs_review requires intent, and refused means invalid input. Keep this MCP configuration out of the neutral agent's tool pool. Encryption/decryption are separate explicit CLI operations."
            ),
        )
        if mode == "operator":
            report["managed_transport"] = {
                "selection": "--peer / peer selects a provisioned destination on send and expected sender on receive",
                "lifecycle": "Use identity-init and recipient-register explicitly; inspect identity-status before digest-bound rotation or revocation. Never put provisioning keys inside a neutral view.",
                "recovery": "Inspect delivery-status. Recover file/extraction with the exact packet and destination; source imports use retained review/snapshots. Revoked keys block pending writes; historical receipts remain readable.",
                "trust": "Local shared-key bindings and retained replay records, not independent identity verification or host isolation",
            }
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
        elif policy is not None:
            selected = AgentPolicy(Workspace(ROOT), policy)
            report["source_policy"] = selected.public_scope()
            report["mcp_config"]["mcpServers"]["bamboozle_agent"]["args"] += [
                "--policy",
                str(selected.path),
            ]
            report["instructions"] = (
                "Use list_safe_files and search_safe_files to discover operator-selected source in neutral vocabulary. Read full files or ranges with read_safe_file; use edit_safe_file with all three source/context/neutral hashes for exact non-overlapping replacements. Use write_safe_file for complete/new files, never ranged excerpts. Read neutral document_rules and generated_directories for the permitted selections. The product applies the configured Python/Markdown/JSON/YAML/text rules on reads and writes; preserve unselected syntax and literal values. Explicit text protect selections keep paths/URLs visible and exact. JSON values_only selects all nested string values, including new values; keys and nonstring tokens remain literal. Keep source_sha256 and context_sha256 from reads for writes after reconnecting. New files use the configured directory rules. Execute the complete returned application. Do not open private policies or dictionaries with raw tools."
            )
    if checks is not None:
        workspace = Workspace(ROOT)
        assert policy is not None
        service = AgentChecks(
            AgentFiles(
                workspace, Dictionary.load(), policy=AgentPolicy(workspace, policy)
            ),
            checks,
        )
        report["mcp_config"]["mcpServers"]["bamboozle_agent"]["args"] += [
            "--checks",
            str(service.path),
        ]
        report["application_checks"] = {
            "checks_sha256": service.fingerprint,
            "check_ids": list(service.specs),
            "tools": sorted(CHECK_TOOLS),
        }
        report["instructions"] += (
            " List configured checks, run under the returned snapshot hash, inspect neutral diagnostics, correct source through the edit/write tools, then list and run again. Retained results survive reconnects; inspect matches_current_source before treating an old pass as current. Check execution uses trusted project code in a neutral copy; it is not an OS sandbox."
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the repository-specific vigiles-vandal_0.2 context and MCP configuration"
    )
    parser.add_argument(
        "--mode", required=True, choices=["agent", "operator", "view", "exchange"]
    )
    parser.add_argument("--view-id")
    parser.add_argument("--exchange-id")
    parser.add_argument("--policy")
    parser.add_argument("--checks")
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                context(
                    args.mode, args.view_id, args.exchange_id, args.policy, args.checks
                ),
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"workflow": WORKFLOW, "status": "error", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
