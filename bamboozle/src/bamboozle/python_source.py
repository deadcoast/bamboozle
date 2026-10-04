"""Explicit Python symbol translation using compiler checks and original token spans.

Selection is an exact spelling contract for project-owned names, including members
and keyword parameters. It is not type inference or substring/snake-case rewriting.
Import paths and external import members stay literal; selected external bindings
need explicit aliases. Dynamic name strings require a separate literal selection.
"""

from __future__ import annotations

import ast
import builtins
import codecs
import io
import keyword
import re
import symtable
import tokenize
import unicodedata
from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from bamboozle.engine import Mode, Translator

MAX_CHARS = 1_000_000
MAX_TOKENS = 200_000
MAX_NODES = 100_000
MAX_SELECTIONS = 512


def identifier(value: str) -> bool:
    return (
        value.isidentifier()
        and not keyword.iskeyword(value)
        and not keyword.issoftkeyword(value)
        and unicodedata.normalize("NFKC", value) == value
        and len(value) <= 512
    )


def selections(
    names: tuple[str, ...], modules: tuple[str, ...], strings: tuple[str, ...]
) -> None:
    for label, values in (
        ("identifiers", names),
        ("modules", modules),
        ("strings", strings),
    ):
        if (
            not isinstance(values, tuple)
            or len(values) > MAX_SELECTIONS
            or any(not isinstance(value, str) for value in values)
            or len(set(values)) != len(values)
        ):
            raise ValueError(
                f"Python {label} must contain at most 512 distinct strings"
            )
    if not names or any(
        not identifier(name) or name.startswith("__") for name in names
    ):
        raise ValueError(
            "Python identifiers require explicit canonical non-keyword names; private/dunder names are not supported"
        )
    for module in modules:
        parts = module.lstrip(".").split(".")
        if (
            len(module) > 1024
            or not module.lstrip(".")
            or any(not identifier(part) for part in parts)
        ):
            raise ValueError(
                "Python modules must be exact dotted import paths, optionally relative"
            )
    if any(not value or len(value) > 4096 for value in strings):
        raise ValueError("Selected Python strings must contain 1 to 4096 characters")


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    value: str
    kind: str


class Source:
    def __init__(self, text: str):
        if len(text) > MAX_CHARS:
            raise ValueError(
                "Python source exceeds the 1000000-character analysis limit"
            )
        self.text = text
        self.bom = int(text.startswith("\ufeff"))
        body = text[self.bom :]
        self.lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", body)
        self.offsets = [self.bom]
        for line in self.lines:
            self.offsets.append(self.offsets[-1] + len(line))
        normalized = body.replace("\r\n", "\n").replace("\r", "\n")
        try:
            encoding, _ = tokenize.detect_encoding(
                io.BytesIO(text.encode("utf-8")).readline
            )
            if codecs.lookup(encoding).name not in ("utf-8", "utf-8-sig"):
                raise ValueError("Python source encoding cookie must declare UTF-8")
            self.tree = ast.parse(normalized, filename="<bamboozle-python>")
            if sum(1 for _ in ast.walk(self.tree)) > MAX_NODES:
                raise ValueError("Python source exceeds the 100000-node analysis limit")
            compile(self.tree, "<bamboozle-python>", "exec", dont_inherit=True)
            self.symbols = symtable.symtable(normalized, "<bamboozle-python>", "exec")
            self.tokens: list[Span] = []
            for token in tokenize.generate_tokens(io.StringIO(normalized).readline):
                if len(self.tokens) >= MAX_TOKENS:
                    raise ValueError(
                        "Python source exceeds the 200000-token analysis limit"
                    )
                self.tokens.append(
                    Span(
                        self.position(*token.start),
                        self.position(*token.end),
                        token.string,
                        tokenize.tok_name[token.type],
                    )
                )
            self.starts = [token.start for token in self.tokens]
        except (SyntaxError, tokenize.TokenError, RecursionError) as exc:
            raise ValueError(f"Invalid or unsupported Python syntax: {exc}") from exc

    def position(self, line: int, column: int, *, byte: bool = False) -> int:
        if line > len(self.lines):
            return len(self.text)
        if byte:
            column = len(self.lines[line - 1].encode("utf-8")[:column].decode("utf-8"))
        return self.offsets[line - 1] + column

    def extent(self, node: Any) -> tuple[int, int]:
        return self.position(node.lineno, node.col_offset, byte=True), self.position(
            node.end_lineno, node.end_col_offset, byte=True
        )

    def within(self, node: ast.AST) -> list[Span]:
        start, end = self.extent(node)
        return [
            token
            for token in self.tokens[
                bisect_left(self.starts, start) : bisect_left(self.starts, end)
            ]
            if token.end <= end and token.kind == "NAME"
        ]

    def fail(self, code: str, message: str, node: Any) -> None:
        line = getattr(node, "lineno", None)
        if line is None:
            line = bisect_right(self.offsets, node.start)
        raise ValueError(f"Python {code} at line {line}: {message}")


class PythonSource:
    def __init__(
        self,
        translator: Translator,
        names: tuple[str, ...],
        modules: tuple[str, ...],
        strings: tuple[str, ...],
    ):
        selections(names, modules, strings)
        self.translator = translator
        self.modules = modules
        self.forward: dict[str, str] = {}
        for name in names:
            if name.casefold() not in translator.dictionary:
                raise ValueError(
                    f"Selected Python identifier needs its own exact dictionary entry: {name}"
                )
            target = translator.forward(name)
            if (
                not identifier(target)
                or target in dir(builtins)
                or translator.dictionary[name.casefold()].startswith("__")
            ):
                raise ValueError(
                    f"Python identifier target must be a non-builtin, non-keyword identifier without a private/dunder prefix: {target}"
                )
            self.forward[name] = target
        if len(set(self.forward.values())) != len(names) or set(self.forward) & set(
            self.forward.values()
        ):
            raise ValueError(
                "Selected Python identifiers must map one-to-one without overlap"
            )
        self.reverse = {target: name for name, target in self.forward.items()}
        self.string_forward = {value: translator.forward(value) for value in strings}
        if len(set(self.string_forward.values())) != len(strings) or set(strings) & set(
            self.string_forward.values()
        ):
            raise ValueError(
                "Selected Python strings must have distinct changed reverse values"
            )
        self.string_reverse = {value: key for key, value in self.string_forward.items()}

    def context(self, context: Any) -> dict[str, Any]:
        if context == {}:
            return {}
        if (
            not isinstance(context, dict)
            or set(context) != {"strings"}
            or not isinstance(context["strings"], list)
            or len(context["strings"]) > MAX_TOKENS
        ):
            raise ValueError("Invalid Python string spelling context")
        size = 0
        for pair in context["strings"]:
            if (
                not isinstance(pair, dict)
                or set(pair) != {"original", "encoded"}
                or any(not isinstance(value, str) for value in pair.values())
            ):
                raise ValueError("Invalid Python literal context entry")
            size += sum(len(value) for value in pair.values())
            if size > self.translator.max_output_chars * 2:
                raise ValueError("Python literal context exceeds its size limit")
            try:
                original, encoded = (
                    ast.literal_eval(pair[key]) for key in ("original", "encoded")
                )
            except (ValueError, SyntaxError, RecursionError) as exc:
                raise ValueError("Python context must contain literal strings") from exc
            if (
                not isinstance(original, str)
                or not isinstance(encoded, str)
                or self.string_forward.get(original) != encoded
                or repr(encoded) != pair["encoded"]
            ):
                raise ValueError(
                    "Python context does not match the selected strings/dictionary"
                )
        return context

    def public_rules(self) -> dict[str, Any]:
        return {
            "format": "python",
            "identifiers": sorted(self.reverse),
            "modules": list(self.modules),
            "strings": sorted(self.string_reverse),
        }

    def _guard(self, source: Source, mode: Mode) -> set[int]:
        mapping = self.forward if mode == "forward" else self.reverse
        strings = self.string_forward if mode == "forward" else self.string_reverse
        protected: set[int] = set()
        external: set[str] = set()
        reflection_aliases: dict[str, str] = {}
        reflection_names = {
            "getattr",
            "setattr",
            "delattr",
            "hasattr",
            "eval",
            "exec",
            "globals",
            "locals",
            "vars",
        }
        nodes = list(ast.walk(source.tree))
        for node in nodes:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                protected.update(token.start for token in source.within(node))
                module = (
                    "." * node.level + (node.module or "")
                    if isinstance(node, ast.ImportFrom)
                    else None
                )
                for alias in node.names:
                    if module == "builtins" and alias.name in reflection_names:
                        reflection_aliases[alias.asname or alias.name] = alias.name
                    tokens = source.within(alias)
                    if alias.name == "*":
                        source.fail(
                            "star_import",
                            "Replace star imports with explicit names before translating",
                            node,
                        )
                    local = (
                        module in self.modules
                        if module is not None
                        else alias.name in self.modules
                    )
                    if isinstance(node, ast.ImportFrom) and local:
                        protected.discard(tokens[0].start)
                    if alias.asname:
                        protected.discard(tokens[-1].start)
                    elif (
                        isinstance(node, ast.Import) or not local
                    ) and alias.name.split(".")[0] in mapping:
                        source.fail(
                            "import_binding",
                            "Keep the external member/module path literal and introduce an explicit local alias",
                            alias,
                        )
                    if isinstance(node, ast.Import) and not local:
                        external.add(alias.asname or alias.name.split(".")[0])
                    if isinstance(node, ast.ImportFrom) and not local:
                        external.add(alias.asname or alias.name)

        imported_external = set(external)
        dependencies: dict[str, set[str]] = defaultdict(set)
        derived_targets: list[tuple[str, ast.Name]] = []
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                value = (
                    node.value.func if isinstance(node.value, ast.Call) else node.value
                )
                while isinstance(value, ast.Attribute):
                    value = value.value
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if isinstance(value, ast.Name):
                    derived_targets.extend(
                        (value.id, target)
                        for target in targets
                        if isinstance(target, ast.Name)
                    )
                    dependencies[value.id].update(
                        target.id for target in targets if isinstance(target, ast.Name)
                    )
        pending = list(external)
        while pending:
            for derived in dependencies[pending.pop()] - external:
                external.add(derived)
                pending.append(derived)
        derived_bindings = {
            id(target) for origin, target in derived_targets if origin in external
        }
        parents = {
            id(child): parent
            for parent in nodes
            for child in ast.iter_child_nodes(parent)
        }

        def root_name(node: ast.AST) -> str | None:
            while isinstance(node, ast.Attribute):
                node = node.value
            return node.id if isinstance(node, ast.Name) else None

        def external_root(node: ast.AST) -> bool:
            return root_name(node) in external

        sensitive = set(imported_external)
        selected_spellings = set(mapping) | set(mapping.values())
        for node in nodes:
            candidate = None
            if isinstance(node, ast.Attribute) and node.attr in selected_spellings:
                candidate = root_name(node)
            elif isinstance(node, ast.Call) and any(
                argument.arg in selected_spellings for argument in node.keywords
            ):
                candidate = root_name(node.func)
            if candidate in external:
                assert candidate is not None
                sensitive.add(candidate)

        for node in nodes:
            if (
                (
                    isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Store)
                    and (
                        node.id in imported_external
                        or (node.id in sensitive and id(node) not in derived_bindings)
                    )
                )
                or (isinstance(node, ast.arg) and node.arg in sensitive)
                or (
                    isinstance(
                        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                    )
                    and node.name in sensitive
                )
            ):
                source.fail(
                    "import_shadow",
                    "Use distinct names for external import aliases and local bindings",
                    node,
                )
            if (
                isinstance(node, (ast.MatchAs, ast.MatchStar, ast.ExceptHandler))
                and node.name in sensitive
            ):
                source.fail(
                    "import_shadow",
                    "Pattern and exception bindings must not shadow external aliases or their derived values",
                    node,
                )
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id
                in reflection_names | set(reflection_aliases) | {"__builtins__"}
            ):
                parent = parents.get(id(node))
                if not isinstance(parent, ast.Call) or parent.func is not node:
                    source.fail(
                        "dynamic_namespace",
                        "Pass reflective operations directly with declared literal references; aliasing or passing them as values is unsupported",
                        node,
                    )
            if isinstance(node, ast.Attribute) and external_root(node):
                protected.add(source.within(node)[-1].start)
            if isinstance(node, ast.Call) and external_root(node.func):
                for argument in node.keywords:
                    if argument.arg is not None:
                        protected.add(source.within(argument)[0].start)
            if isinstance(node, ast.Call):
                name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else ""
                )
                name = reflection_aliases.get(name, name)
                if name in {
                    "eval",
                    "exec",
                    "globals",
                    "locals",
                    "vars",
                    "__getattribute__",
                    "__setattr__",
                    "__delattr__",
                }:
                    source.fail(
                        "dynamic_namespace",
                        "Replace dynamic namespace evaluation/access with explicit references before translation",
                        node,
                    )
                if name in {"getattr", "setattr", "delattr", "hasattr"}:
                    if (
                        len(node.args) < 2
                        or not isinstance(node.args[1], ast.Constant)
                        or not isinstance(node.args[1].value, str)
                    ):
                        source.fail(
                            "dynamic_member",
                            "Dynamic member lookup needs a literal, explicitly selected name",
                            node,
                        )
                    member_argument = node.args[1]
                    assert isinstance(member_argument, ast.Constant)
                    member_value = member_argument.value
                    if member_value in mapping and member_value not in strings:
                        source.fail(
                            "unselected_reference",
                            "Select the member-name literal with --python-string as well as its identifier",
                            node.args[1],
                        )
            if isinstance(node, ast.Attribute) and node.attr == "__dict__":
                source.fail(
                    "dynamic_namespace",
                    "Direct __dict__ access cannot establish static symbol references",
                    node,
                )
            reference_nodes: list[ast.AST] = []
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if (
                    any(
                        isinstance(target, ast.Name)
                        and target.id in {"__all__", "__slots__"}
                        for target in targets
                    )
                    and node.value
                ):
                    reference_nodes.append(node.value)
            if isinstance(node, ast.arg) and node.annotation:
                reference_nodes.append(node.annotation)
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.returns
            ):
                reference_nodes.append(node.returns)
            if isinstance(node, ast.AnnAssign):
                reference_nodes.append(node.annotation)
            if isinstance(node, ast.keyword) and node.arg is None:
                reference_nodes.append(node.value)
            for reference in reference_nodes:
                for literal in ast.walk(reference):
                    if isinstance(literal, ast.Constant) and isinstance(
                        literal.value, str
                    ):
                        selected = any(
                            re.search(r"\b" + re.escape(name) + r"\b", literal.value)
                            for name in mapping
                        )
                        if selected and literal.value not in strings:
                            source.fail(
                                "unselected_reference",
                                "Select this symbolic string explicitly with --python-string",
                                literal,
                            )

        # A whole-spelling bijection preserves shadowing/free/global/nonlocal names.
        # Reject unbound builtin references rather than silently renaming Python APIs.
        root_bound = {
            symbol.get_name()
            for symbol in source.symbols.get_symbols()
            if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
        }
        tables = [source.symbols]
        while tables:
            table = tables.pop()
            tables.extend(table.get_children())
            for symbol in table.get_symbols():
                if (
                    symbol.get_name() in mapping
                    and symbol.get_name() in dir(builtins)
                    and symbol.is_referenced()
                    and symbol.is_global()
                    and symbol.get_name() not in root_bound
                ):
                    raise ValueError(
                        f"Python builtin_reference: {symbol.get_name()} is not a project binding; choose another identifier selection"
                    )
        return protected

    def spans(self, text: str, mode: Mode = "reverse") -> tuple[Source, list[Span]]:
        source = Source(text)
        protected = self._guard(source, mode)
        strings = self.string_forward if mode == "forward" else self.string_reverse
        whole_literals = {
            source.extent(node)
            for node in ast.walk(source.tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        spans = []
        for token in source.tokens:
            if (
                token.kind == "NAME"
                and token.start not in protected
                and not keyword.iskeyword(token.value)
            ):
                spans.append(token)
            elif token.kind == "STRING":
                value = ast.literal_eval(token.value)
                if (
                    mode == "forward"
                    and isinstance(value, str)
                    and value in strings.values()
                ):
                    source.fail(
                        "string_collision",
                        "An existing literal collides with a selected neutral string; choose a distinct mapping or narrower source preparation",
                        token,
                    )
                if isinstance(value, str) and value in strings:
                    if (token.start, token.end) not in whole_literals:
                        source.fail(
                            "concatenated_literal",
                            "Do not translate part of an implicitly concatenated string; select one complete literal token",
                            token,
                        )
                    spans.append(Span(token.start, token.end, value, "STRING"))
        # Concatenated literals represent one semantic value, not separate strings.
        selected_spans = {(span.start, span.end) for span in spans}
        for node in ast.walk(source.tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in strings
            ):
                start, end = source.extent(node)
                if (start, end) not in selected_spans:
                    source.fail(
                        "concatenated_literal",
                        "Use one literal token for an explicitly selected string",
                        node,
                    )
        return source, spans

    def transform(
        self, text: str, mode: Mode, context: dict[str, Any]
    ) -> tuple[str, int, dict[str, Any]]:
        source, spans = self.spans(text, mode)
        mapping = self.forward if mode == "forward" else self.reverse
        strings = self.string_forward if mode == "forward" else self.string_reverse
        saved: dict[str, deque[str]] = defaultdict(deque)
        for pair in self.context(context).get("strings", []):
            saved[pair["encoded"]].append(pair["original"])
        retained = []
        replacements = []
        size = len(text.encode("utf-8"))
        for span in spans:
            raw = text[span.start : span.end]
            if span.kind == "STRING":
                rendered = repr(strings[span.value])
                if mode == "reverse" and saved[raw]:
                    rendered = saved[raw].popleft()
                if mode == "forward":
                    retained.append({"original": raw, "encoded": rendered})
            else:
                normalized = unicodedata.normalize("NFKC", span.value)
                if normalized in mapping.values():
                    source.fail(
                        "name_collision",
                        f"Unselected identifier {span.value!r} collides with a selected {'neutral' if mode == 'forward' else 'source'} spelling; rename it or narrow selection",
                        span,
                    )
                if normalized not in mapping:
                    continue
                if normalized != span.value:
                    source.fail(
                        "normalized_identifier",
                        "Selected identifier spelling must be NFKC canonical",
                        span,
                    )
                rendered = mapping[span.value]
            size += len(rendered.encode("utf-8")) - len(raw.encode("utf-8"))
            if size > self.translator.max_output_chars:
                raise ValueError("Translated Python exceeds its byte limit")
            replacements.append((span.start, span.end, rendered))
        pieces: list[str] = []
        cursor = 0
        for start, end, rendered in replacements:
            pieces.extend((text[cursor:start], rendered))
            cursor = end
        pieces.append(text[cursor:])
        result = "".join(pieces)
        Source(result)
        return result, len(replacements), {"strings": retained} if retained else {}
