"""Tree-sitter entity extraction for Python and TypeScript (deterministic, offline).

An *entity* is an addressable code unit — a function, class, or method — named by a
scope-qualified path (``Bar.m``) that mirrors the effect-model address space in
``sgt/effects/model.py:units`` so a Python entity lines up with the effect targets and
blame spans that paint it. Extraction is pure over the source text: same bytes in, same
entity list out (no LLM, no network, no dict-order nondeterminism — tree-sitter's walk is
deterministic and we never sort by anything but document order).

Coverage is whole-repo by design (origin R1/R5): files in an unsupported language, and
syntactically-broken files, yield zero entities rather than raising — the map shows them as
honest unattributed structure, it does not choke on them.

Each entity also carries two body hashes, computed here because this is the one place with the
parsed AST in hand (ported from ``references/sem`` — reference only, not a dependency):
``content_hash`` over the exact node bytes answers "did the text change at all", and
``structural_hash`` over normalized AST tokens (comments stripped, whitespace trimmed) answers
"is this the same code modulo formatting/comments" — the signal a rename/move detector needs
and one a line-range differ cannot produce. They are in-memory identity signals, kept out of
``to_dict`` so the read projection stays lean.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from tree_sitter import Language, Parser

import tree_sitter_python as _tsp
import tree_sitter_typescript as _tst


@dataclass(frozen=True)
class Entity:
    """One code unit. ``name`` is scope-qualified (``Class.method``); ``id`` is repo-unique."""

    id: str  # f"{file}::{name}" — unique across the repo
    name: str  # scope-qualified path within the file, e.g. "Bar.m"
    file: str  # repo-relative path
    kind: str  # "function" | "class" | "method"
    start_line: int  # 1-based inclusive
    end_line: int  # 1-based inclusive
    container: str | None  # enclosing scope-qualified name, or None for top level
    content_hash: str = ""  # hash of the exact body bytes — "did the text change"
    structural_hash: str = ""  # hash of normalized AST tokens — "same code modulo formatting"

    def to_dict(self) -> dict:
        # Hashes are deliberately omitted: they are an in-memory diff signal, not part of
        # the read projection (keeps the entity-graph view lean and golden fixtures stable).
        return {
            "id": self.id,
            "name": self.name,
            "file": self.file,
            "kind": self.kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "container": self.container,
        }


# -- language registry -------------------------------------------------------
# Built lazily and cached: constructing a Language/Parser is cheap but not free, and a
# whole-repo parse hits the same two languages thousands of times.
_LANGS: dict[str, Language] = {}


def _language(lang: str) -> Language:
    if lang not in _LANGS:
        if lang == "python":
            _LANGS[lang] = Language(_tsp.language())
        elif lang == "typescript":
            _LANGS[lang] = Language(_tst.language_typescript())
        elif lang == "tsx":
            _LANGS[lang] = Language(_tst.language_tsx())
        else:  # pragma: no cover - guarded by _language_for
            raise ValueError(f"unknown language {lang!r}")
    return _LANGS[lang]


_EXT_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
}

# Per-language map of def-bearing node types -> base kind. "method" vs "function" is then
# refined by enclosing scope (a Python function inside a class is a method).
_DEFS: dict[str, dict[str, str]] = {
    "python": {
        "function_definition": "function",
        "class_definition": "class",
    },
    "typescript": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "interface_declaration": "class",
        "method_definition": "method",
    },
    "tsx": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "interface_declaration": "class",
        "method_definition": "method",
    },
}

# TS arrow/function-expression bound to a name (`const foo = () => {}`) is the dominant way
# functions are declared in modern TS; treat the declarator as a function entity.
_ARROW_VALUES = {"arrow_function", "function_expression"}


def _language_for(path: str) -> str | None:
    for ext, lang in _EXT_LANG.items():
        if path.endswith(ext):
            return lang
    return None


# Comment node types across the supported grammars — stripped from the structural hash so a
# comment/docstring edit doesn't read as a code change.
_COMMENT_NODES = {"comment", "line_comment", "block_comment", "doc_comment", "tag_comment"}


def _content_hash(node, src: bytes) -> str:
    """Hash the entity's exact bytes: any textual change (including formatting) flips it."""
    return hashlib.sha1(src[node.start_byte : node.end_byte]).hexdigest()


def _structural_hash(node, src: bytes) -> str:
    """Streaming hash of the AST — a Python port of sem's ``hash_structural_tokens``.

    Interior nodes contribute their type (so ``x = f(y)`` and ``f(y) = x`` differ despite equal
    leaves); leaves contribute their whitespace-trimmed text; comments are skipped. The result
    is invariant to reformatting and comment edits but sensitive to real structural change."""
    h = hashlib.sha1()
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in _COMMENT_NODES:
            continue
        if n.child_count == 0:
            leaf = src[n.start_byte : n.end_byte].strip()
            if leaf:
                h.update(leaf)
                h.update(b" ")
        else:
            h.update(n.type.encode("utf-8"))
            h.update(b":")
            stack.extend(reversed(n.children))  # pop yields children in source order
    return h.hexdigest()


def _def_entity(node, base_defs, path, prefix):
    """Return ``(leaf_name, base_kind, name_node)`` if ``node`` is a def, else None."""
    if node.type in base_defs:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        return name_node.text.decode("utf-8", "replace"), base_defs[node.type], node
    # `const foo = () => {}` / `const foo = function () {}`
    if node.type == "variable_declarator":
        value = node.child_by_field_name("value")
        name_node = node.child_by_field_name("name")
        if value is not None and name_node is not None and value.type in _ARROW_VALUES:
            return name_node.text.decode("utf-8", "replace"), "function", node
    return None


def extract_file(path: str, source: str, *, language: str | None = None) -> list[Entity]:
    """Parse one file's source into entities. Unsupported/unparseable -> ``[]`` (never raises)."""
    lang = language or _language_for(path)
    if lang is None:
        return []
    parser = Parser(_language(lang))
    base_defs = _DEFS[lang]
    src = bytes(source, "utf-8")
    tree = parser.parse(src)  # tree-sitter never raises; bad syntax -> ERROR nodes

    out: list[Entity] = []

    def walk(node, stack: list[tuple[str, str]]) -> None:
        child_stack = stack
        hit = _def_entity(node, base_defs, path, stack)
        if hit is not None:
            leaf, base_kind, span_node = hit
            prefix = [name for name, _ in stack]
            name = ".".join([*prefix, leaf])
            container = ".".join(prefix) or None
            kind = base_kind
            if kind == "function" and stack and stack[-1][1] == "class":
                kind = "method"
            out.append(
                Entity(
                    id=f"{path}::{name}",
                    name=name,
                    file=path,
                    kind=kind,
                    start_line=span_node.start_point[0] + 1,
                    end_line=span_node.end_point[0] + 1,
                    container=container,
                    content_hash=_content_hash(span_node, src),
                    structural_hash=_structural_hash(span_node, src),
                )
            )
            child_stack = [*stack, (leaf, kind)]
        for child in node.children:
            walk(child, child_stack)

    walk(tree.root_node, [])
    return out


def extract_codebase(codebase: dict[str, str]) -> list[Entity]:
    """Extract entities across a whole ``{path: source}`` map, in sorted-path order."""
    out: list[Entity] = []
    for path in sorted(codebase):
        out.extend(extract_file(path, codebase[path]))
    return out
