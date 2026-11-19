"""Tree-sitter entity extraction for Python and TypeScript (JavaScript through the TypeScript
grammars). An entity is a function, class, method, top-level binding or import, named by its
scope-qualified path (``Bar.m``). Extraction is a pure function of the source bytes: spans are byte
offsets into them, an unsupported or unparseable file yields no entities, and a span includes the
decorators or ``export`` that belong to it. Same-name hits that sit together (``@overload`` stubs, a
property's getter and setter) fold into one entity; others get an ordinal suffix. Each entity
carries a content hash of its bytes and a structural hash that ignores formatting and comments.
"""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from dataclasses import dataclass

from tree_sitter import Language, Node, Parser

import tree_sitter_python as _tsp
import tree_sitter_typescript as _tst


@dataclass(frozen=True)
class Entity:
    """One code unit. ``name`` is scope-qualified (``Class.method``); ``id`` is repo-unique.
    Content is sliced by ``start_byte``/``end_byte`` only; the line numbers are for display."""

    id: str  # f"{file}::{name}" — unique across the repo
    name: str  # scope-qualified path within the file, e.g. "Bar.m"
    file: str  # repo-relative path
    kind: str  # "function" | "class" | "method" | "binding" | "import"
    start_line: int  # 1-based inclusive, display only
    end_line: int  # 1-based inclusive, display only
    container: str | None  # enclosing scope-qualified name, or None for top level
    content_hash: str = ""  # hash of the exact span bytes — "did the text change"
    structural_hash: str = ""  # hash of normalized AST tokens — "same code modulo formatting"
    start_byte: int = 0  # byte offset of the entity's span, decorators/export included
    end_byte: int = 0  # byte offset one past the end of the entity's span

    def to_dict(self) -> dict:
        # hashes and byte offsets are in-memory signals, left out of the projection
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
    # JavaScript parses with the TypeScript grammars, a syntactic superset, so no extra dependency
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

# The parent node that wraps a decorated or exported def; climbing to it takes in its decorators
# and `export`.
_WRAPPER_PARENT = {"python": "decorated_definition", "typescript": "export_statement", "tsx": "export_statement"}

_DECLARATION_TYPES = {"lexical_declaration", "variable_declaration"}

# Root-level imports are entities, so one can be taken out on its own. An import nested in a block
# (`if TYPE_CHECKING:`) stays residue.
_IMPORT_TYPES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement", "future_import_statement"},
    "typescript": {"import_statement", "export_statement"},
    "tsx": {"import_statement", "export_statement"},
}


def _import_specifier(node: Node, src: bytes, lang: str) -> str | None:
    """The module an import statement names, or None if the node is not a sourced import.

    The specifier -- not the bound names -- keys the entity, because one statement is one
    contiguous span and may bind many names (`import { rank, weight } from './rank'`). A
    TypeScript `export_statement` only qualifies when it re-exports (`export { x } from './m'`);
    a plain `export const x = 1` is a declaration and is already an entity via `_climb_declaration`."""
    source = node.child_by_field_name("source")  # TS import/re-export
    if source is not None:
        return src[source.start_byte : source.end_byte].decode("utf-8", "replace").strip("\"'")
    if node.type == "export_statement":
        return None  # `export const ...` / `export default ...` -- not a re-export
    if lang == "python":
        if node.type == "future_import_statement":
            return "__future__"
        module = node.child_by_field_name("module_name")  # `from X import ...`
        if module is not None:
            return src[module.start_byte : module.end_byte].decode("utf-8", "replace")
        names = [  # `import X, Y` -- no single source; the dotted names *are* the specifier
            src[c.start_byte : c.end_byte].decode("utf-8", "replace")
            for c in node.children
            if c.type in ("dotted_name", "aliased_import")
        ]
        return ", ".join(names) if names else None
    return None


def _import_entities(root: Node, src: bytes, path: str, lang: str) -> list[Entity]:
    """One entity per top-level import statement, named by module specifier.

    Two statements may legally share a specifier (`import X from './m'` beside
    `import { y } from './m'`), so a repeat gets a `#n` suffix in document order -- stable as long
    as their relative order is, which is the same guarantee every other positional fact here has."""
    types = _IMPORT_TYPES.get(lang, set())
    out: list[Entity] = []
    seen: dict[str, int] = {}
    for node in root.children:
        if node.type not in types:
            continue
        spec = _import_specifier(node, src, lang)
        if spec is None:
            continue
        end = _owned_end(node, src)
        seen[spec] = seen.get(spec, 0) + 1
        # `__import__::` marker, not a bare `import:` prefix: `_symbol_kind` splits entity from
        # nested on "does the name contain a dot", and specifiers are full of dots.
        name = f"__import__::{spec}" if seen[spec] == 1 else f"__import__::{spec}#{seen[spec]}"
        out.append(
            Entity(
                id=f"{path}::{name}",
                name=name,
                file=path,
                kind="import",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                container=None,  # top level: this is what makes the residue partition split here
                content_hash=hashlib.sha1(src[node.start_byte : end]).hexdigest(),
                structural_hash=_structural_hash_range(node, node, src),
                start_byte=node.start_byte,
                end_byte=end,
            )
        )
    return out


def _owned_end(node: Node, src: bytes) -> int:
    """A root statement's end, its line terminator included, so taking the statement out leaves
    an empty gap rather than a newline the fold would move to the end of the file."""
    end = node.end_byte
    return end + 1 if src[end : end + 1] == b"\n" else end


_NAME_NODES = {"identifier", "type_identifier", "shorthand_property_identifier_pattern"}
_PATTERN_FIELD = {"pair_pattern": "value", "object_assignment_pattern": "left", "assignment_pattern": "left"}
_PATTERN_NODES = {
    "pattern_list", "tuple_pattern", "list_pattern", "tuple", "list", "parenthesized_expression",
    "list_splat_pattern", "list_splat", "dictionary_splat_pattern", "as_pattern_target",
    "object_pattern", "array_pattern", "rest_pattern",
}


def binding_targets(node: Node | None) -> list[str]:
    """The names an assignment target or destructuring pattern binds (`a`, `a, b`, `{ x, y: [z] }`)."""
    if node is None:
        return []
    if node.type in _NAME_NODES:
        return [node.text.decode("utf-8", "replace")]
    if node.type in _PATTERN_FIELD:
        return binding_targets(node.child_by_field_name(_PATTERN_FIELD[node.type]))
    if node.type in _PATTERN_NODES:
        return [n for c in node.named_children for n in binding_targets(c)]
    return []


def binding_names(node: Node, lang: str) -> list[str]:
    """The names a root statement binds when it is a binding entity (a module-level assignment,
    `const`/`let`/`var`, `type`, `enum`), else []. A declaration holding an arrow function or
    function expression is not one: those declarators are function entities already."""
    if lang == "python":
        if node.type != "expression_statement" or not node.named_children:
            return []
        names: list[str] = []
        stmt = node.named_children[0]
        while stmt is not None and stmt.type in ("assignment", "augmented_assignment"):
            names += binding_targets(stmt.child_by_field_name("left"))
            stmt = stmt.child_by_field_name("right")
        return names
    decl = node.child_by_field_name("declaration") if node.type == "export_statement" else node
    if decl is None:
        return []
    if decl.type in ("type_alias_declaration", "enum_declaration"):
        return binding_targets(decl.child_by_field_name("name"))
    if decl.type not in _DECLARATION_TYPES:
        return []
    declarators = [c for c in decl.named_children if c.type == "variable_declarator"]
    if any((d.child_by_field_name("value") or d).type in _ARROW_VALUES for d in declarators):
        return []
    return [n for d in declarators for n in binding_targets(d.child_by_field_name("name"))]


def _language_for(path: str) -> str | None:
    for ext, lang in _EXT_LANG.items():
        if path.endswith(ext):
            return lang
    return None


# Comment node types across the supported grammars — stripped from the structural hash so a
# comment/docstring edit doesn't read as a code change.
_COMMENT_NODES = {"comment", "line_comment", "block_comment", "doc_comment", "tag_comment"}


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
    """Widen leftward over the ``decorator`` nodes just before ``node``: a TypeScript class member's
    decorator is its sibling, not its parent."""
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
    end_byte: int


def _coalesce(hits: list[_Hit], all_hits_by_name: dict[str, list[_Hit]], src: bytes) -> list[_Hit]:
    """Fold a same-name group into one entity when no differently-named entity lies within its
    byte range (`@overload` stubs, a getter beside its setter); otherwise keep each with an ordinal
    suffix, so ids stay unique per file and nothing is dropped."""
    if len(hits) == 1:
        return hits
    ordered = sorted(hits, key=lambda h: h.start_node.start_byte)
    lo = ordered[0].start_node.start_byte
    hi = ordered[-1].end_node.end_byte

    def _contained_in_a_member(h: _Hit) -> bool:
        # a member's own nested children (a method of a duplicated class) do not block the merge
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
            end_byte=ordered[-1].end_byte,
        )
        return [merged]
    out: list[_Hit] = []
    for i, h in enumerate(ordered):
        name = h.name if i == 0 else f"{h.name}${i + 1}"
        out.append(_Hit(name=name, kind=h.kind, container=h.container, start_node=h.start_node,
                        end_node=h.end_node, end_byte=h.end_byte))
    return out


# (path, language, sha256 of source) -> entities, LRU. The lists are shared: callers must not
# mutate them.
_EXTRACT_CACHE: "OrderedDict[tuple, list[Entity]]" = OrderedDict()
_EXTRACT_CACHE_MAX = 4096

# The cache persists to `.sgt/local/extract_cache.json` between `mine()`'s attach and flush: every
# entry touched in between, so the next process re-parses only changed files.
# Bump on any change to what extract_file returns for unchanged input; the key cannot see it.
_EXTRACT_WALK = 3

_PERSIST_REPO = None
_PERSIST_SEEN: set[tuple] = set()  # keys touched since the last flush
_PERSIST_MISSES = 0  # fresh parses since the last flush

_ENTITY_FIELDS = ("id", "name", "file", "kind", "start_line", "end_line", "container",
                  "content_hash", "structural_hash", "start_byte", "end_byte")


def attach_persistent_cache(repo) -> None:
    """Point the cache's disk backing at `repo` and load it. Idempotent per repo; entries from
    another repo are harmless, since keys are content-addressed."""
    global _PERSIST_REPO
    if _PERSIST_REPO is not None and os.path.realpath(_PERSIST_REPO) == os.path.realpath(repo):
        return
    _PERSIST_REPO = repo
    from ugh import state

    body = state.load_json(repo, "extract_cache", default=None)
    if not isinstance(body, dict) or body.get("walk") != _EXTRACT_WALK:
        return  # another extractor shape: every persisted list is stale
    entries = body.get("entries")
    if not isinstance(entries, dict):
        return
    for skey, rows in entries.items():
        lang, _, rest = skey.partition("\x00")
        fpath, _, hexdigest = rest.partition("\x00")
        try:
            key = (fpath, lang, bytes.fromhex(hexdigest))
            ents = [Entity(**dict(zip(_ENTITY_FIELDS, row, strict=True))) for row in rows]
        except (TypeError, ValueError):
            continue  # a bad entry is parsed again
        _EXTRACT_CACHE.setdefault(key, ents)
    while len(_EXTRACT_CACHE) > _EXTRACT_CACHE_MAX:
        _EXTRACT_CACHE.popitem(last=False)


def flush_persistent_cache() -> None:
    """Persist every entry touched since the last flush and reset the touched set. Nothing is
    written when detached or when nothing new was parsed."""
    global _PERSIST_MISSES
    if _PERSIST_REPO is None or _PERSIST_MISSES == 0 or not _PERSIST_SEEN:
        return
    _PERSIST_MISSES = 0
    from ugh import state

    entries = {}
    for key in _PERSIST_SEEN:
        ents = _EXTRACT_CACHE.get(key)
        if ents is None:
            continue
        fpath, lang, digest = key
        entries[f"{lang}\x00{fpath}\x00{digest.hex()}"] = [
            [getattr(e, f) for f in _ENTITY_FIELDS] for e in ents
        ]
    _PERSIST_SEEN.clear()
    state.save_json(_PERSIST_REPO, "extract_cache", {"walk": _EXTRACT_WALK, "entries": entries})


def extract_file(path: str, source: bytes | str) -> list[Entity]:
    """Parse one file's source into entities; an unsupported language gives ``[]`` and nothing
    raises. A `str` source is encoded as UTF-8. Identical bytes return the cached list."""
    lang = _language_for(path)
    if lang is None:
        return []
    src = source if isinstance(source, bytes) else source.encode("utf-8")
    cache_key = (path, lang, hashlib.sha256(src).digest())
    if _PERSIST_REPO is not None:
        _PERSIST_SEEN.add(cache_key)
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
            hits.append(_Hit(name=name, kind=kind, container=container, start_node=start_node,
                             end_node=end_node, end_byte=end_node.end_byte))
            child_stack = [*stack, (leaf, kind)]
        for child in node.children:
            walk(child, child_stack)

    for child in tree.root_node.children:
        names = binding_names(child, lang)
        if names:
            hits.append(_Hit(name=names[0], kind="binding", container=None, start_node=child,
                             end_node=child, end_byte=_owned_end(child, src)))
            walk(child, [(names[0], "binding")])
        else:
            walk(child, [])

    by_name: dict[str, list[_Hit]] = {}
    for h in hits:
        by_name.setdefault(h.name, []).append(h)

    out: list[Entity] = []
    for name in by_name:  # document order
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
                    content_hash=hashlib.sha1(src[h.start_node.start_byte : h.end_byte]).hexdigest(),
                    structural_hash=_structural_hash_range(h.start_node, h.end_node, src),
                    start_byte=h.start_node.start_byte,
                    end_byte=h.end_byte,
                )
            )
    out.extend(_import_entities(tree.root_node, src, path, lang))
    _EXTRACT_CACHE[cache_key] = out
    if len(_EXTRACT_CACHE) > _EXTRACT_CACHE_MAX:
        _EXTRACT_CACHE.popitem(last=False)
    if _PERSIST_REPO is not None:
        global _PERSIST_MISSES
        _PERSIST_MISSES += 1
    return out


def extract_codebase(codebase: dict[str, bytes | str]) -> list[Entity]:
    """Extract entities across a whole ``{path: source}`` map, in sorted-path order."""
    out: list[Entity] = []
    for path in sorted(codebase):
        out.extend(extract_file(path, codebase[path]))
    return out
