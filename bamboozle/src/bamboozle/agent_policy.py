"""Operator-owned document selection for the direct semantic agent interface."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from bamboozle.formats import FormatRules
from bamboozle.project_views import document_rules, read_policy
from bamboozle.utils import sha256
from bamboozle.workspace import Workspace


class AgentPolicy:
    """Reuse view rule syntax without exporting a view or exposing private rules.

    Explicit files take precedence. Prefix rules also govern subsequent reads of
    generated files; every existing-file write still requires a matching read.
    Fixtures require views and are refused here instead of reading production data.
    """

    def __init__(self, workspace: Workspace, name: str):
        self.workspace = workspace
        self.path = workspace.resolve(name)
        if not self.path.name.casefold().endswith(".agent-policy.json"):
            raise ValueError(
                "Source-agent policies require the private .agent-policy.json suffix"
            )
        raw = workspace.read(self.path)
        self.document = read_policy(raw)
        self.fingerprint = sha256(raw)
        if any(rule["policy"] == "fixture" for rule in self.document["files"].values()):
            raise ValueError("Fixture policies require a neutral project view")

    def recheck(self) -> None:
        if sha256(self.workspace.read(self.path)) != self.fingerprint:
            raise ValueError("Source-agent policy changed; reconnect and read again")

    def select(self, path: Path) -> tuple[str, FormatRules]:
        self.recheck()
        name = path.relative_to(self.workspace.root).as_posix()
        if any(
            name.casefold() == selected.casefold() and name != selected
            for selected in self.document["files"]
        ):
            raise ValueError("Use the exact file spelling from the source-agent policy")
        rule: Any = self.document["files"].get(name)
        if rule is None:
            matches = [
                prefix
                for prefix in self.document["new_files"]
                if prefix == "."
                or PurePosixPath(name.casefold()).is_relative_to(
                    PurePosixPath(prefix.casefold())
                )
            ]
            if not matches:
                raise ValueError("File is outside the operator's source-agent policy")
            prefix = max(matches, key=lambda value: len(PurePosixPath(value).parts))
            if prefix != "." and not PurePosixPath(name).is_relative_to(
                PurePosixPath(prefix)
            ):
                raise ValueError(
                    "Use the exact directory spelling from the source-agent policy"
                )
            rule = self.document["new_files"][prefix]
        action = rule["policy"] if isinstance(rule, dict) else rule
        if action == "exclude":
            raise ValueError("File is excluded by the operator's source-agent policy")
        return action, document_rules(rule)

    def public_scope(self) -> dict[str, Any]:
        return {
            "policy_sha256": self.fingerprint,
            "files": [
                name
                for name, rule in self.document["files"].items()
                if rule["policy"] != "exclude"
            ],
            "generated_directories": list(self.document["new_files"]),
            "formats": sorted(
                {
                    document_rules(rule).format
                    for rule in [
                        *self.document["files"].values(),
                        *self.document["new_files"].values(),
                    ]
                    if rule != "copy"
                    and (not isinstance(rule, dict) or rule["policy"] == "translate")
                }
            ),
        }
