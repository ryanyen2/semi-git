"""Assemble extracted entities into a connected graph.

Edges are *contains* (a class contains its methods) and *uses*. Inside a definition, a name refers
to the definition the language binds it to: a top-level definition of that name in the same file,
or the definition an import in that file binds the name to. Nothing else is resolved; a member
access on an object of unknown type yields no edge.
"""

from __future__ import annotations

import hashlib
import posixpath
from collections import OrderedDict
from dataclasses import dataclass

from tree_sitter import Parser

from ugh.entities.extract import (
    Entity,
    _language,
    _language_for,
    binding_names,
    binding_targets,
    extract_codebase,
)


@dataclass(frozen=True)
class EntityEdge:
    src: str  # entity id
    dst: str  # entity id
    type: str  # "contains" | "uses"


@dataclass
class EntityGraph:
    entities: list[Entity]
    edges: list[EntityEdge]


# -- per-file binding walk ---------------------------------------------------
# scopes: nodes that open a scope. declares: nodes whose `name` binds in the enclosing scope.
# binds: node -> the field holding the names it binds in the current scope. reads: name nodes.
# chains: member access node -> (object field, property field). skip: node -> a field that is
# not a read (a declared name, a property, a keyword argument's name).
_PY_DECLARES = {"function_definition", "class_definition"}
_PY = {
    "scopes": {"function_definition", "lambda", "list_comprehension", "set_comprehension",
               "dictionary_comprehension", "generator_expression", "class_definition"},
    "declares": _PY_DECLARES,
    "binds": {"assignment": "left", "augmented_assignment": "left", "named_expression": "name",
              "for_statement": "left", "for_in_clause": "left", "as_pattern": "alias"},
    "reads": {"identifier"},
    "chains": {"attribute": ("object", "attribute")},
    "skip": {"attribute": "attribute", "keyword_argument": "name", **dict.fromkeys(_PY_DECLARES, "name")},
    "self": {"self", "cls"},
}
_TS_DECLARES = {"function_declaration", "generator_function_declaration", "class_declaration",
                "abstract_class_declaration", "interface_declaration", "type_alias_declaration",
                "enum_declaration"}
_TS = {
    "scopes": {"function_declaration", "generator_function_declaration", "function_expression",
               "generator_function", "arrow_function", "method_definition", "class_declaration",
               "abstract_class_declaration", "class", "interface_declaration", "type_alias_declaration"},
    "declares": _TS_DECLARES,
    "binds": {"variable_declarator": "name", "required_parameter": "pattern",
              "optional_parameter": "pattern", "catch_clause": "parameter", "for_in_statement": "left",
              "type_parameter": "name", "arrow_function": "parameter"},
    "reads": {"identifier", "type_identifier", "shorthand_property_identifier", "this"},
    "chains": {"member_expression": ("object", "property"), "nested_type_identifier": ("module", "name")},
    "skip": {"nested_type_identifier": "name", **dict.fromkeys(_TS_DECLARES, "name")},
    "self": {"this"},
}
_JSX_ELEMENTS = {"jsx_opening_element", "jsx_closing_element", "jsx_self_closing_element"}
_TS_EXTS = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs")


def _text(node) -> str:
    return node.text.decode("utf-8", "replace")


def _import_bindings(node, py: bool) -> list[tuple[str, str, str | None]] | None:
    """`(local name, module specifier, imported name or None for a module)` for each name an
    import statement binds, or None when `node` is not an import."""
    if py:
        if node.type == "import_statement":
            out = []
            for c in node.children_by_field_name("name"):
                if c.type == "aliased_import":
                    out.append((_text(c.child_by_field_name("alias")), _text(c.child_by_field_name("name")), None))
                else:
                    top = _text(c).split(".")[0]  # `import a.b` binds `a`
                    out.append((top, top, None))
            return out
        if node.type == "import_from_statement":
            module = _text(node.child_by_field_name("module_name"))
            out = []
            for c in node.children_by_field_name("name"):
                if c.type == "aliased_import":
                    out.append((_text(c.child_by_field_name("alias")), module, _text(c.child_by_field_name("name"))))
                else:
                    out.append((_text(c), module, _text(c)))
            return out
        return [] if node.type == "future_import_statement" else None
    if node.type not in ("import_statement", "export_statement"):
        return None
    source = node.child_by_field_name("source")
    if source is None:
        return None
    module = _text(source).strip("\"'`")
    out = []
    for c in node.named_children:
        if c.type == "import_clause":
            for part in c.named_children:
                if part.type == "identifier":
                    out.append((_text(part), module, "default"))
                elif part.type == "namespace_import":
                    out.extend((_text(i), module, None) for i in part.named_children if i.type == "identifier")
                elif part.type == "named_imports":
                    out.extend(_specifiers(part, module))
        elif c.type == "export_clause":
            out.extend(_specifiers(c, module))
    return out


def _specifiers(node, module: str) -> list[tuple[str, str, str]]:
    out = []
    for spec in node.named_children:
        name, alias = spec.child_by_field_name("name"), spec.child_by_field_name("alias")
        if name is not None:
            out.append((_text(alias or name), module, _text(name)))
    return out


def _owner_index(ents: list[Entity]):
    """`owner_at(pos)`: the innermost entity whose span holds byte `pos`. Positions must not
    decrease between calls, which a document-order walk guarantees."""
    spans = sorted((e.start_byte, -e.end_byte, e.name) for e in ents)
    stack: list[tuple[int, str]] = []
    i = 0

    def owner_at(pos: int) -> str | None:
        nonlocal i
        while i < len(spans) and spans[i][0] <= pos:
            start, neg_end, name = spans[i]
            while stack and stack[-1][0] <= start:
                stack.pop()
            stack.append((-neg_end, name))
            i += 1
        while stack and stack[-1][0] <= pos:
            stack.pop()
        return stack[-1][1] if stack else None

    return owner_at


def _scope_lookup(chain, name: str):
    """What the innermost enclosing scope binding `name` binds it to: None for a local, a
    `(module, imported)` pair for a function-level import. `_FREE` when no scope binds it."""
    for scope, _ in reversed(chain):
        if name in scope:
            return scope[name]
    return _FREE


_FREE = object()


def _walk(path: str, src: bytes, ents: list[Entity]) -> dict:
    """One file's binding facts, a pure function of its bytes (entity names do not depend on path):
    `names` (top-level name -> entity, what an importer of this file can reach), `local` (same-file
    `(owner, target)` uses) and `remote` (`(owner, module, names)` uses resolved against other files)."""
    lang = _language_for(path)
    py = lang == "python"
    tab = _PY if py else _TS
    owner_at = _owner_index(ents)
    names = {e.name: e.name for e in ents if e.container is None and e.kind != "import"}
    imports: dict[str, tuple[str | None, str, str | None]] = {}
    aliases: list[tuple[str, str]] = []
    raw: list[tuple[str, str, tuple[str, ...], tuple]] = []
    members: set[tuple[str, str]] = set()

    def bind(chain, node) -> None:
        if chain and node is not None:
            chain[-1][0].update(dict.fromkeys(binding_targets(node)))

    def walk(node, chain) -> None:
        t = node.type
        bound = _import_bindings(node, py)
        if bound is not None:
            if chain:
                chain[-1][0].update((local, (module, imported)) for local, module, imported in bound)
            else:
                imp = owner_at(node.start_byte)
                imports.update((local, (imp, module, imported)) for local, module, imported in bound)
            return
        if t in tab["reads"]:
            read(node, chain)
            return
        if t == "export_statement" and not chain:
            export_aliases(node)
        if t in tab["declares"]:
            bind(chain, node.child_by_field_name("name"))
        if t in tab["scopes"]:
            outer = tuple(s for s in chain if not s[1]) if py else chain
            chain = (*outer, ({}, t == "class_definition"))
        if t in tab["binds"]:
            bind(chain, node.child_by_field_name(tab["binds"][t]))
        if t in ("parameters", "lambda_parameters"):
            for p in node.named_children:
                bind(chain, p.child_by_field_name("name") or (p.named_children[0] if p.type == "typed_parameter" else p))
        skip = node.child_by_field_name(tab["skip"][t]) if t in tab["skip"] else None
        for c in node.children:
            if skip is None or c != skip:
                walk(c, chain)

    def read(node, chain) -> None:
        name = _text(node)
        parent = node.parent
        if parent.type in _JSX_ELEMENTS and name[:1].islower() and parent.child_by_field_name("name") == node:
            return
        attrs: list[str] = []
        n = node
        while (p := n.parent) is not None and p.type in tab["chains"]:
            obj, prop = (p.child_by_field_name(f) for f in tab["chains"][p.type])
            if obj != n or prop is None:
                break
            attrs.append(_text(prop))
            n = p
        own = owner_at(node.start_byte)
        if own is None:
            return
        if name in tab["self"]:
            if attrs:
                members.add((own, attrs[0]))
            return
        raw.append((own, name, tuple(attrs), chain))

    def export_aliases(node) -> None:
        decl = node.child_by_field_name("declaration")
        if any(c.type == "default" for c in node.children):
            target = decl.child_by_field_name("name") if decl is not None else node.child_by_field_name("value")
            if target is not None and target.type in ("identifier", "type_identifier"):
                aliases.append(("default", _text(target)))
        for c in node.named_children:
            if c.type == "export_clause":
                aliases.extend((alias, name) for alias, _, name in _specifiers(c, ""))

    tree = Parser(_language(lang)).parse(src)
    for child in tree.root_node.children:
        extra = binding_names(child, lang)[1:]
        if extra:
            owner = owner_at(child.start_byte)
            for n in extra:
                names.setdefault(n, owner)
        walk(child, ())
    for exported_name, local in aliases:
        if local in names:
            names[exported_name] = names[local]
        elif local in imports:
            imports[exported_name] = imports[local]

    local_uses: set[tuple[str, str]] = set()
    remote: set[tuple[str, str, tuple[str, ...]]] = set()
    for own, name, attrs, chain in raw:
        b = _scope_lookup(chain, name)
        if b is None:
            continue
        if b is not _FREE:
            remote.add((own, b[0], ((b[1],) if b[1] else ()) + attrs))
        elif name in names:
            local_uses.add((own, names[name]))
        elif name in imports:
            imp, module, imported = imports[name]
            if imp:
                local_uses.add((own, imp))
            remote.add((own, module, ((imported,) if imported else ()) + attrs))
    kinds = {e.name: e.kind for e in ents}
    for own, attr in members:
        parts = own.split(".")
        for i in range(len(parts) - 1, 0, -1):
            cls = ".".join(parts[:i])
            if kinds.get(cls) == "class":
                if f"{cls}.{attr}" in kinds:
                    local_uses.add((own, f"{cls}.{attr}"))
                break
    for imp, module, imported in imports.values():
        if imp and imported:
            remote.add((imp, module, (imported,)))
    return {
        "names": {**{k: v[0] for k, v in imports.items() if v[0]}, **names},
        "local": sorted((o, t) for o, t in local_uses if o != t),
        "remote": sorted((o, m, p) for o, m, p in remote if p and (py or m.startswith("."))),
    }


# (language, source digest) -> `_walk` facts, read-only: consecutive commits share most files.
_REFS_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_REFS_CACHE_MAX = 4096

# Persisted to `.sgt/local/refs_cache.json` with the same attach/flush contract as `extract.py`'s.
_REFS_PERSIST_REPO = None
_REFS_SEEN: set[tuple] = set()  # keys touched since the last flush
_REFS_MISSES = 0

# The cache key is (language, source bytes) with no room for the walk itself: bump this whenever
# `_walk` changes what it returns, so entries written by another walk are dropped on load.
_REFS_WALK = 4


def attach_persistent_refs_cache(repo) -> None:
    """Point the refs cache's disk backing at `repo`'s `.sgt/local/` and load it. Idempotent per
    repo."""
    global _REFS_PERSIST_REPO
    import os

    if _REFS_PERSIST_REPO is not None and os.path.realpath(_REFS_PERSIST_REPO) == os.path.realpath(repo):
        return
    _REFS_PERSIST_REPO = repo
    from ugh import state

    body = state.load_json(repo, "refs_cache", default=None)
    if not isinstance(body, dict) or body.get("walk") != _REFS_WALK:
        return  # written by a different walk -- re-parse rather than trust it
    entries = body.get("entries")
    if not isinstance(entries, dict):
        return
    for skey, facts in entries.items():
        lang, _, hexdigest = skey.partition("\x00")
        try:
            key = (lang, bytes.fromhex(hexdigest))
        except ValueError:
            continue  # a bad entry is parsed again
        if isinstance(facts, dict):
            _REFS_CACHE.setdefault(key, facts)
    while len(_REFS_CACHE) > _REFS_CACHE_MAX:
        _REFS_CACHE.popitem(last=False)


def flush_persistent_refs_cache() -> None:
    """Persist every entry touched since the last flush, then reset the touched set. No-op when
    detached or when nothing new was parsed. Called by the verbs that attached (never per-file)."""
    global _REFS_MISSES
    if _REFS_PERSIST_REPO is None or _REFS_MISSES == 0 or not _REFS_SEEN:
        return
    _REFS_MISSES = 0
    from ugh import state

    entries = {}
    for key in _REFS_SEEN:
        facts = _REFS_CACHE.get(key)
        if facts is None:
            continue
        lang, digest = key
        entries[f"{lang}\x00{digest.hex()}"] = facts
    _REFS_SEEN.clear()
    state.save_json(_REFS_PERSIST_REPO, "refs_cache", {"walk": _REFS_WALK, "entries": entries})


def _file_facts(path: str, source: bytes | str, ents: list[Entity]) -> dict:
    src = source if isinstance(source, bytes) else source.encode("utf-8")
    key = (_language_for(path), hashlib.sha256(src).digest())
    if _REFS_PERSIST_REPO is not None:
        _REFS_SEEN.add(key)
    cached = _REFS_CACHE.get(key)
    if cached is not None:
        _REFS_CACHE.move_to_end(key)
        return cached
    facts = _walk(path, src, ents)
    _REFS_CACHE[key] = facts
    if len(_REFS_CACHE) > _REFS_CACHE_MAX:
        _REFS_CACHE.popitem(last=False)
    if _REFS_PERSIST_REPO is not None:
        global _REFS_MISSES
        _REFS_MISSES += 1
    return facts


def _import_roots(files) -> list[str]:
    """The repo root and every directory that holds top-level Python modules or packages without
    being a package itself (no `__init__.py`): `src/`, `scripts/`, ..."""
    roots = {""}
    for p in files:
        if p.endswith((".py", ".pyi")):
            d = posixpath.dirname(p)
            while d and f"{d}/__init__.py" in files:
                d = posixpath.dirname(d)
            roots.add(d)
    return sorted(roots)


def _module_file(importer: str, spec: str, files, roots: list[str]) -> str | None:
    """The file a module specifier names from `importer`. Python: `a.b` -> `a/b.py` or
    `a/b/__init__.py`, under the importer's package for leading dots, else under each import root
    (none when two roots name different files). TS: `./m` -> `m.ts`, `m.tsx`, ..., `m/index.ts`, ...
    Anything else (a package, the standard library) is not in the codebase."""
    if _language_for(importer) == "python":
        dots = len(spec) - len(spec.lstrip("."))
        parts = [p for p in spec[dots:].split(".") if p]
        if dots:
            base = importer.split("/")[:-1]
            if dots - 1 > len(base):
                return None
            stems = ["/".join(base[: len(base) - dots + 1] + parts)]
        else:
            stems = [posixpath.join(root, *parts) for root in roots]
        found = set()
        for s in stems:
            hit = next((c for c in (f"{s}.py", f"{s}/__init__.py", f"{s}.pyi", f"{s}/__init__.pyi") if c in files), None)
            if hit:
                found.add(hit)
        return found.pop() if len(found) == 1 else None
    else:
        stem = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
        root, ext = posixpath.splitext(stem)
        bases = [stem, root] if ext in (".js", ".jsx", ".mjs", ".cjs") else [stem]
        cands = [stem, *(b + e for b in bases for e in _TS_EXTS), *(f"{stem}/index{e}" for e in _TS_EXTS)]
    return next((c for c in cands if c in files), None)


# -- assembly ----------------------------------------------------------------
def build_entity_graph(codebase: dict[str, str]) -> EntityGraph:
    """The entities of `codebase` (`{path: source}`) with their contains and uses edges."""
    entities = extract_codebase(codebase)
    by_id = {e.id: e for e in entities}
    ents_by_file: dict[str, list[Entity]] = {}
    for e in entities:
        ents_by_file.setdefault(e.file, []).append(e)

    contains: list[EntityEdge] = []
    for e in entities:
        if e.container is not None:
            parent_id = f"{e.file}::{e.container}"
            if parent_id in by_id:
                contains.append(EntityEdge(parent_id, e.id, "contains"))

    facts = {f: _file_facts(f, codebase[f], ents) for f, ents in sorted(ents_by_file.items())}
    roots = _import_roots(codebase)
    modules: dict[tuple[str, str, bool], str | None] = {}

    def module(importer: str, spec: str) -> str | None:
        key = (posixpath.dirname(importer), spec, importer.endswith((".py", ".pyi")))
        if key not in modules:
            modules[key] = _module_file(importer, spec, codebase, roots)
        return modules[key]

    def lookup(importer: str, spec: str, path) -> str | None:
        """The definition `spec`'s `path` names (`from spec import path[0]`, `m.path[0]`), walking
        Python submodules (`import a` then `a.b.f`)."""
        py = _language_for(importer) == "python"
        mod = module(importer, spec)
        for name in path:
            target = facts[mod]["names"].get(name) if mod in facts else None
            if target:
                return f"{mod}::{target}"
            if not py:
                return None
            spec = spec + name if spec.endswith(".") else f"{spec}.{name}"
            mod = module(importer, spec)
        return None

    uses: list[EntityEdge] = []
    seen: set[tuple[str, str]] = set()
    for file, fx in facts.items():
        pairs = [(f"{file}::{o}", f"{file}::{t}") for o, t in fx["local"]]
        for own, spec, path in fx["remote"]:
            target = lookup(file, spec, path)
            if target:
                pairs.append((f"{file}::{own}", target))
        for pair in pairs:
            if pair not in seen and pair[0] != pair[1]:
                seen.add(pair)
                uses.append(EntityEdge(*pair, "uses"))

    return EntityGraph(entities, contains + uses)
