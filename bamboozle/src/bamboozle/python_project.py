"""Check declared Python import relationships in a complete selected view snapshot."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import PurePosixPath

from bamboozle.python_source import Source


def validate_project(
    files: dict[str, bytes], modules: dict[str, tuple[str, ...]]
) -> dict:
    """Validate both sides of declared imports without executing repository code.

    The Python runtime still decides import execution order and dynamic values.
    This check establishes that selected providers exist and bind imported names.
    Undeclared external packages are not loaded, inspected or rewritten.
    """
    index: dict[str, list[str]] = defaultdict(list)
    for name in files:
        path = PurePosixPath(name)
        if path.suffix == ".py":
            parts = list(path.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            index[".".join(parts)].append(name)
    parsed: dict[str, Source] = {}

    def source(name: str) -> Source:
        if name not in parsed:
            parsed[name] = Source(files[name].decode("utf-8"))
        return parsed[name]

    def resolve(importer: str, module: str) -> str:
        if module.startswith("."):
            levels = len(module) - len(module.lstrip("."))
            parts = list(PurePosixPath(importer).parent.parts)
            if levels > len(parts):
                raise ValueError(
                    f"Python project import escapes its selected package: {importer}: {module}"
                )
            module = ".".join(
                [*parts[: len(parts) - levels + 1], module.lstrip(".")]
            ).rstrip(".")
        candidates = index.get(module, [])
        if not candidates:
            candidates = [
                name
                for path, names in index.items()
                if path.endswith("." + module)
                for name in names
            ]
        if len(candidates) != 1:
            raise ValueError(
                f"Python project import provider is missing or ambiguous: {importer}: {module}"
            )
        return candidates[0]

    count = 0
    relationships = []
    for name, declared in sorted(modules.items()):
        program = source(name)
        aliases: dict[str, str] = {}
        for node in ast.walk(program.tree):
            if isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                if module not in declared:
                    continue
                provider = resolve(name, module)
                symbols = {
                    symbol.get_name()
                    for symbol in source(provider).symbols.get_symbols()
                    if symbol.is_assigned()
                    or symbol.is_imported()
                    or symbol.is_namespace()
                }
                for alias in node.names:
                    if alias.name not in symbols:
                        raise ValueError(
                            f"Python project import is not bound by its provider: {name}:{node.lineno}: {module}.{alias.name}; align selected identifiers and exported definitions"
                        )
                    count += 1
                relationships.append(
                    {
                        "source": name,
                        "provider": provider,
                        "line": node.lineno,
                        "names": [alias.name for alias in node.names],
                    }
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in declared:
                        provider = resolve(name, alias.name)
                        # Attribute checks support explicit aliases and simple modules.
                        aliases[alias.asname or alias.name] = provider
                        relationships.append(
                            {
                                "source": name,
                                "provider": provider,
                                "line": node.lineno,
                                "names": [],
                            }
                        )
        for node in ast.walk(program.tree):
            if not isinstance(node, ast.Attribute):
                continue
            parts = [node.attr]
            value = node.value
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if not isinstance(value, ast.Name):
                continue
            parts.append(value.id)
            parts.reverse()
            attribute_provider = aliases.get(".".join(parts[:-1]))
            if attribute_provider is not None:
                symbols = {
                    symbol.get_name()
                    for symbol in source(attribute_provider).symbols.get_symbols()
                    if symbol.is_assigned()
                    or symbol.is_imported()
                    or symbol.is_namespace()
                }
                if parts[-1] not in symbols:
                    raise ValueError(
                        f"Python project attribute is not bound by its provider: {name}:{node.lineno}: {'.'.join(parts)}"
                    )
                count += 1
    return {
        "status": "checked",
        "files": len(modules),
        "providers_parsed": len(parsed),
        "imported_members_checked": count,
        "relationships": relationships,
        "runtime_execution_required": True,
    }
