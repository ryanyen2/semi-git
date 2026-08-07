"""Tree-sitter entity extraction for Python and TypeScript (deterministic, offline).

An *entity* is an addressable code unit — a function, class, or method — named by a
scope-qualified path (``Bar.m``) that mirrors the effect-model address space in
``sgt/effects/model.py:units`` so a Python entity lines up with the effect targets and
blame spans that paint it. Extraction is pure over the source **bytes**: same bytes in, same
entity list out (no LLM, no network, no dict-order nondeterminism — tree-sitter's walk is
deterministic and we never sort by anything but document order).

Coverage is whole-repo by design (origin R1/R5): files in an unsupported language, and
syntactically-broken files, yield zero entities rather than raising — the map shows them as
honest unattributed structure, it does not choke on them.

Byte-native by construction (kernel byte-fidelity audit, 2026-07-08): tree-sitter parses raw
bytes directly and every entity's span is addressed by ``start_byte``/``end_byte`` sliced
straight from those bytes — never through a decoded ``str`` round-trip. This is what makes
extraction correct on CRLF line endings, non-UTF-8 files, and any byte sequence a decode-then-
reslice pipeline would corrupt or truncate (form feeds, line separators inside string literals).
``start_line``/``end_line`` survive only as a display/blame convenience derived from
tree-sitter's own row count; they are never used to slice content.

An entity's span always includes any decorator/``export`` wrapper that grammatically belongs to
it (Python's ``decorated_definition`` parent; TypeScript's ``export_statement`` parent, or a
class-body member's preceding ``decorator`` sibling) — see ``_entity_span``. Without this, a
decorator attaches to whichever entity happens to render first after materialization, which is
silent semantic corruption, not a formatting nit.

A file can legitimately produce more than one raw hit for the same scope-qualified name
(``@overload`` stubs, a ``@property`` getter next to its ``@x.setter``, a ``@x.register``
overload). ``_coalesce`` folds a contiguous group (no differently-named entity's span between
its members) into one ``Entity`` whose span is the verbatim union of its members — one logical
symbol, matching how the language treats them. A non-contiguous collision (rare: a conditional
redefinition) falls back to a stable document-order ordinal suffix so two different id's worth
of code is never silently collapsed into one.

Each entity also carries two body hashes, computed here because this is the one place with the
parsed AST in hand (ported from ``references/sem`` — reference only, not a dependency):
``content_hash`` over the exact span bytes answers "did the text change at all", and
``structural_hash`` over normalized AST tokens (comments stripped, whitespace trimmed) answers
"is this the same code modulo formatting/comments" — the signal a rename/move detector needs
and one a line-range differ cannot produce. They are in-memory identity signals, kept out of
``to_dict`` so the read projection stays lean.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass

from tree_sitter import Language, Node, Parser

import tree_sitter_python as _tsp
import tree_sitter_typescript as _tst


@dataclass(frozen=True)
class Entity:
    """One code unit. ``name`` is scope-qualified (``Class.method``); ``id`` is repo-unique.

    ``start_byte``/``end_byte`` are the entity's true span in the file's raw bytes -- the only
    addressing this kernel slices content by. ``start_line``/``end_line`` (1-based, inclusive)
    are a display/blame derivative, kept for callers that only need a human-readable range."""

    id: str  # f"{file}::{name}" — unique across the repo
    name: str  # scope-qualified path within the file, e.g. "Bar.m"
    file: str  # repo-relative path
    kind: str  # "function" | "class" | "method"
    start_line: int  # 1-based inclusive, display only
    end_line: int  # 1-based inclusive, display only
    container: str | None  # enclosing scope-qualified name, or None for top level
    content_hash: str = ""  # hash of the exact span bytes — "did the text change"
    structural_hash: str = ""  # hash of normalized AST tokens — "same code modulo formatting"
    start_byte: int = 0  # byte offset of the entity's span, decorators/export included
    end_byte: int = 0  # byte offset one past the end of the entity's span

    def to_dict(self) -> dict:
        # Hashes and byte offsets are deliberately omitted: in-memory diff/addressing signals,
        # not part of the read projection (keeps the entity-graph view lean and golden fixtures
        # stable).
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
    # JavaScript reuses the TypeScript grammars: TS is a syntactic superset of JS, so every
    # def-bearing node in `_DEFS["typescript"]`/`["tsx"]` parses the same out of a `.js`/`.jsx` file.
    # Without these entries a JS/React repo produced no symbol-level ops at all -- every file fell
    # through `_language_for` to a single whole-file symbol, which quietly removes the entire point
    # of sgt (features, blame, and revert are all symbol-scoped) for a large share of real projects.
    # Deliberately not a new dependency: `tree-sitter-javascript` would add a grammar for syntax the
    # installed one already accepts (CLAUDE.md §8).
    ".js": "typescript",
    ".mjs": "typescript",
    ".cjs": "typescript",
    ".jsx": "tsx",
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

# The single node type that wraps a decorated def as its parent, per language -- climbing to it
# absorbs every decorator (Python) or the `export` keyword plus any decorator riding along with
# it (TypeScript's `export_statement`, confirmed empirically: `export_statement`'s children are
# `[decorator?, export, default?, <declaration>]`, all siblings inside one wrapper).
_WRAPPER_PARENT = {"python": "decorated_definition", "typescript": "export_statement", "tsx": "export_statement"}

_DECLARATION_TYPES = {"lexical_declaration", "variable_declaration"}


def _language_for(path: str) -> str | None:
    for ext, lang in _EXT_LANG.items():
        if path.endswith(ext):
            return lang
    return None


# Comment node types across the supported grammars — stripped from the structural hash so a
# comment/docstring edit doesn't read as a code change.
_COMMENT_NODES = {"comment", "line_comment", "block_comment", "doc_comment", "tag_comment"}


def _content_hash_range(start: Node, end: Node, src: bytes) -> str:
    """Hash the verbatim bytes from ``start``'s beginning to ``end``'s end -- a single slice,
    since decorator-widening and collision-coalescing only ever combine sibling nodes that are
    already contiguous (or safely so) in the source. Any textual change flips it."""
    return hashlib.sha1(src[start.start_byte : end.end_byte]).hexdigest()


def _structural_hash_range(start: Node, end: Node, src: bytes) -> str:
    """Streaming hash of the AST from ``start`` through ``end`` (inclusive) via sibling links --
    a Python port of sem's ``hash_structural_tokens``, extended to span >1 sibling root so a
    decorator or a coalesced duplicate-name group hashes as one unit. Interior nodes contribute
    their type (so ``x = f(y)`` and ``f(y) = x`` differ despite equal leaves); leaves contribute
    their whitespace-trimmed text; comments are skipped. Invariant to reformatting/comments,
    sensitive to real structural change."""
    h = hashlib.sha1()
    node: Node | None = start
    while node is not None:
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
        if node == end:
            break
        node = node.next_sibling
    return h.hexdigest()


def _widen_over_decorator_siblings(node: Node) -> Node:
    """Widen leftward over any contiguous ``decorator`` nodes immediately preceding ``node`` in
    the same parent -- covers TypeScript class-body members, whose decorator is a *sibling* of
    the ``method_definition``/field it decorates, not a child or a wrapping parent (confirmed
    empirically: `@HostListener('click')` sits beside `onClick()` inside `class_body`)."""
    start = node
    prev = start.prev_sibling
    while prev is not None and prev.type == "decorator":
        start = prev
        prev = start.prev_sibling
    return start


def _climb_declaration(node: Node, lang: str) -> Node:
    """Climb from a def-bearing node to the outermost node that exists solely to
    decorate/export it -- Python's `decorated_definition` parent, or TypeScript's
    `export_statement` parent (one hop directly for a declaration, two hops for an arrow bound
    via `const`/`let`, and only when that declaration owns exactly one declarator -- a multi-decl
    `export const a = 1, b = 2` must not swallow both bindings into `a`'s span)."""
    cur = node
    if node.type == "variable_declarator":
        parent = node.parent
        if parent is not None and parent.type in _DECLARATION_TYPES:
            declarators = [c for c in parent.children if c.type == "variable_declarator"]
            if len(declarators) == 1:
                cur = parent
    wrapper = _WRAPPER_PARENT.get(lang)
    grandparent = cur.parent
    if wrapper is not None and grandparent is not None and grandparent.type == wrapper:
        return grandparent
    return cur


def _entity_span(def_node: Node, lang: str) -> tuple[Node, Node]:
    """The `(start_node, end_node)` sibling pair whose combined byte range is this entity's true
    span: climb through an export/decorator wrapper parent, then widen over any decorator
    siblings the climb didn't already absorb."""
    climbed = _climb_declaration(def_node, lang)
    start_node = _widen_over_decorator_siblings(climbed)
    return start_node, climbed


def _def_entity(node: Node, base_defs: dict[str, str]):
    """Return ``(leaf_name, base_kind, def_node)`` if ``node`` is a def, else None. ``def_node``
    is the bare def/declarator node -- span widening happens at the call site via
    ``_entity_span``, once, after the base kind/name are known."""
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


@dataclass
class _Hit:
    """One raw walk hit, before duplicate-name coalescing/disambiguation."""

    name: str
    kind: str
    container: str | None
    start_node: Node
    end_node: Node


def _coalesce(hits: list[_Hit], all_hits_by_name: dict[str, list[_Hit]], src: bytes) -> list[_Hit]:
    """Fold a same-name group into one entity where safe, else disambiguate with a stable
    ordinal so no two entities ever collide on `id` (`_symbol_kind`'s "unique id per file"
    invariant that every downstream consumer -- footprints, identity, the fold -- relies on).

    Mergeable iff the group is *contiguous*: no other, differently-named entity's span falls
    within the group's own outer byte range. `@overload` stubs beside their implementation, or a
    `@property` getter beside its `@x.setter`, satisfy this (any code between them is ordinary
    residue, safely absorbed into the merged span verbatim); a name genuinely re-declared with
    unrelated code between the declarations does not, and keeps its own separate identity via an
    ordinal suffix rather than being silently dropped."""
    if len(hits) == 1:
        return hits
    ordered = sorted(hits, key=lambda h: h.start_node.start_byte)
    lo = ordered[0].start_node.start_byte
    hi = ordered[-1].end_node.end_byte

    def _contained_in_a_member(h: _Hit) -> bool:
        # A nested child of one of *this group's own* members (e.g. a method inside a
        # duplicated class) sits inside the envelope by construction -- that's containment,
        # not interference, and must not block the merge.
        return any(
            m.start_node.start_byte <= h.start_node.start_byte and h.end_node.end_byte <= m.end_node.end_byte
            for m in ordered
        )

    foreign = [
        h
        for others in all_hits_by_name.values()
        if others is not hits
        for h in others
        if lo <= h.start_node.start_byte and h.end_node.end_byte <= hi and not _contained_in_a_member(h)
    ]
    if not foreign:
        merged = _Hit(
            name=ordered[0].name,
            kind=ordered[0].kind,
            container=ordered[0].container,
            start_node=ordered[0].start_node,
            end_node=ordered[-1].end_node,
        )
        return [merged]
    # Non-contiguous collision (rare: a conditional redefinition) -- keep the first occurrence's
    # name, disambiguate the rest with a stable, document-order ordinal so the id space stays
    # unique without losing any entity's content.
    out: list[_Hit] = []
    for i, h in enumerate(ordered):
        name = h.name if i == 0 else f"{h.name}${i + 1}"
        out.append(_Hit(name=name, kind=h.kind, container=h.container, start_node=h.start_node, end_node=h.end_node))
    return out


# Content-addressed extraction cache (U10): `extract_file` is a pure function of (path, language,
# source bytes), so a file byte-identical to one parsed earlier -- the common case across
# consecutive commits, which touch a handful of files but re-parse the whole tree -- reuses its
# entity list instead of re-running tree-sitter. Bounded LRU: mining walks history in order, so a
# file's old content versions fall out of the working set naturally; the cap keeps a full-history
# init from growing unbounded. Entities are frozen and the list is treated read-only by every
# caller (`extract_codebase` extends a fresh list), so the cached list is shared, not copied.
_EXTRACT_CACHE: "OrderedDict[tuple, list[Entity]]" = OrderedDict()
_EXTRACT_CACHE_MAX = 4096


def extract_file(path: str, source: bytes | str, *, language: str | None = None) -> list[Entity]:
    """Parse one file's source into entities. Unsupported/unparseable -> ``[]`` (never raises).

    `source` is bytes-native throughout -- pass raw bytes (e.g. a git blob) whenever they're
    available; a `str` is accepted for ergonomic hand-written callers/fixtures and encoded once
    via UTF-8 (lossless, since a Python `str` is already correctly decoded). Content-addressed
    cache (U10): a byte-identical re-parse of the same path/language returns the cached list."""
    lang = language or _language_for(path)
    if lang is None:
        return []
    src = source if isinstance(source, bytes) else source.encode("utf-8")
    cache_key = (path, lang, hashlib.sha256(src).digest())
    cached = _EXTRACT_CACHE.get(cache_key)
    if cached is not None:
        _EXTRACT_CACHE.move_to_end(cache_key)
        return cached
    parser = Parser(_language(lang))
    base_defs = _DEFS[lang]
    tree = parser.parse(src)  # tree-sitter never raises; bad syntax -> ERROR nodes

    hits: list[_Hit] = []

    def walk(node: Node, stack: list[tuple[str, str]]) -> None:
        child_stack = stack
        hit = _def_entity(node, base_defs)
        if hit is not None:
            leaf, base_kind, def_node = hit
            prefix = [name for name, _ in stack]
            name = ".".join([*prefix, leaf])
            container = ".".join(prefix) or None
            kind = base_kind
            if kind == "function" and stack and stack[-1][1] == "class":
                kind = "method"
            start_node, end_node = _entity_span(def_node, lang)
            hits.append(_Hit(name=name, kind=kind, container=container, start_node=start_node, end_node=end_node))
            child_stack = [*stack, (leaf, kind)]
        for child in node.children:
            walk(child, child_stack)

    walk(tree.root_node, [])

    by_name: dict[str, list[_Hit]] = {}
    for h in hits:
        by_name.setdefault(h.name, []).append(h)

    out: list[Entity] = []
    for name in by_name:  # dict preserves first-seen (document) order -- deterministic
        for h in _coalesce(by_name[name], by_name, src):
            out.append(
                Entity(
                    id=f"{path}::{h.name}",
                    name=h.name,
                    file=path,
                    kind=h.kind,
                    start_line=h.start_node.start_point[0] + 1,
                    end_line=h.end_node.end_point[0] + 1,
                    container=h.container,
                    content_hash=_content_hash_range(h.start_node, h.end_node, src),
                    structural_hash=_structural_hash_range(h.start_node, h.end_node, src),
                    start_byte=h.start_node.start_byte,
                    end_byte=h.end_node.end_byte,
                )
            )
    _EXTRACT_CACHE[cache_key] = out
    if len(_EXTRACT_CACHE) > _EXTRACT_CACHE_MAX:
        _EXTRACT_CACHE.popitem(last=False)
    return out


def extract_codebase(codebase: dict[str, bytes | str]) -> list[Entity]:
    """Extract entities across a whole ``{path: source}`` map, in sorted-path order."""
    out: list[Entity] = []
    for path in sorted(codebase):
        out.extend(extract_file(path, codebase[path]))
    return out
