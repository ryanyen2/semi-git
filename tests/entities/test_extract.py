"""U1 — tree-sitter entity extraction (Python + TypeScript)."""

from __future__ import annotations

from sgt.entities import Entity, extract_codebase, extract_file


def _by_name(ents: list[Entity]) -> dict[str, Entity]:
    return {e.name: e for e in ents}


def test_python_function_class_and_nested_method():
    src = (
        "def foo(x):\n"
        "    return x\n"
        "\n"
        "class Bar:\n"
        "    def m(self):\n"
        "        return 1\n"
    )
    ents = _by_name(extract_file("kg/models.py", src))
    assert set(ents) == {"foo", "Bar", "Bar.m"}
    assert ents["foo"].kind == "function" and ents["foo"].container is None
    assert ents["Bar"].kind == "class"
    # A function nested in a class is a method, scope-qualified under the class.
    assert ents["Bar.m"].kind == "method"
    assert ents["Bar.m"].container == "Bar"
    # Line ranges are 1-based inclusive.
    assert ents["foo"].start_line == 1 and ents["foo"].end_line == 2
    assert ents["Bar.m"].start_line == 5 and ents["Bar.m"].end_line == 6
    # id is repo-unique (file-prefixed).
    assert ents["Bar.m"].id == "kg/models.py::Bar.m"


def test_python_async_function_resolves_scope():
    src = "class S:\n    async def fetch(self):\n        return 2\n"
    ents = _by_name(extract_file("kg/io.py", src))
    assert ents["S.fetch"].kind == "method"
    assert ents["S.fetch"].container == "S"


def test_typescript_function_class_method():
    src = (
        "export function answer(q: string): string {\n"
        "  return q;\n"
        "}\n"
        "export class KnowledgeGraph {\n"
        "  neighbors(id: string): string[] {\n"
        "    return [];\n"
        "  }\n"
        "}\n"
    )
    ents = _by_name(extract_file("web/kg.ts", src))
    assert "answer" in ents and ents["answer"].kind == "function"
    assert "KnowledgeGraph" in ents and ents["KnowledgeGraph"].kind == "class"
    assert "KnowledgeGraph.neighbors" in ents
    assert ents["KnowledgeGraph.neighbors"].kind == "method"
    assert ents["KnowledgeGraph.neighbors"].container == "KnowledgeGraph"


def test_typescript_arrow_const_is_a_function():
    src = "export const submit = (e: Event) => {\n  e.preventDefault();\n};\n"
    ents = _by_name(extract_file("web/portal.ts", src))
    assert "submit" in ents and ents["submit"].kind == "function"


def test_empty_comment_only_and_broken_files_yield_no_entities_without_raising():
    assert extract_file("a.py", "") == []
    assert extract_file("a.py", "# just a comment\n") == []
    # Broken syntax must not raise — tree-sitter produces ERROR nodes, we extract what parses.
    assert extract_file("a.py", "def (:\n  ???") == []
    # Unsupported extension is skipped entirely.
    assert extract_file("README.md", "# title\n") == []


def test_extraction_is_deterministic():
    src = "def a():\n    return 1\nclass C:\n    def b(self):\n        return 2\n"
    first = [e.to_dict() for e in extract_file("m.py", src)]
    second = [e.to_dict() for e in extract_file("m.py", src)]
    assert first == second


def test_extract_codebase_sorted_and_whole_repo():
    cb = {
        "z.py": "def z():\n    return 1\n",
        "a.ts": "export function a(): void {}\n",
        "notes.txt": "ignored",
    }
    ents = extract_codebase(cb)
    files = [e.file for e in ents]
    # Sorted-path order; the unsupported .txt contributes nothing.
    assert files == ["a.ts", "z.py"]


def test_structural_hash_ignores_formatting_and_comments():
    """The rename/move key: reformatting and comment edits leave structural_hash stable while
    content_hash tracks the raw bytes."""
    plain = _by_name(extract_file("m.py", "def f(x):\n    return x + 1\n"))["f"]
    reformatted = _by_name(
        extract_file("m.py", "def f(x):\n    # add one\n    return  x  +  1\n")
    )["f"]
    assert plain.structural_hash == reformatted.structural_hash  # formatting/comments don't count
    assert plain.content_hash != reformatted.content_hash  # but the raw text did change


def test_hashes_flip_on_real_body_change():
    before = _by_name(extract_file("m.py", "def f(x):\n    return x + 1\n"))["f"]
    after = _by_name(extract_file("m.py", "def f(x):\n    return x + 2\n"))["f"]
    assert before.content_hash != after.content_hash
    assert before.structural_hash != after.structural_hash


def test_hashes_are_deterministic_and_populated():
    ents = extract_file("m.py", "def a():\n    return 1\nclass C:\n    def b(self):\n        return 2\n")
    again = extract_file("m.py", "def a():\n    return 1\nclass C:\n    def b(self):\n        return 2\n")
    for e, e2 in zip(ents, again):
        assert e.content_hash and e.structural_hash  # every entity carries both
        assert e.content_hash == e2.content_hash and e.structural_hash == e2.structural_hash


def test_extraction_cache_reuses_identical_content_and_is_content_addressed():
    """U10: `extract_file` caches by (path, language, content) -- a byte-identical re-parse returns
    the cached list (the same object, since Entities are frozen and callers treat it read-only),
    while a content change re-parses. The cache is content-addressed, so it never returns a stale
    entity list for changed bytes, and never crosses paths."""
    from sgt.entities import extract as extract_mod

    extract_mod._EXTRACT_CACHE.clear()
    src = "def a():\n    return 1\n"
    first = extract_file("m.py", src)
    assert extract_file("m.py", src) is first          # byte-identical -> cached object
    assert extract_file("m.py", src.encode()) is first  # str and bytes of the same content agree

    changed = extract_file("m.py", "def a():\n    return 2\n")
    assert changed is not first                         # different content -> re-parsed
    assert [e.name for e in changed] == ["a"]

    other_path = extract_file("n.py", src)              # same content, different path
    assert other_path is not first                      # keyed by path too
    assert other_path[0].id == "n.py::a"


def test_extraction_cache_is_bounded():
    """The LRU cap keeps a full-history init from growing the cache without bound."""
    from sgt.entities import extract as extract_mod

    extract_mod._EXTRACT_CACHE.clear()
    for i in range(extract_mod._EXTRACT_CACHE_MAX + 50):
        extract_file(f"f{i}.py", f"def g():\n    return {i}\n")
    assert len(extract_mod._EXTRACT_CACHE) == extract_mod._EXTRACT_CACHE_MAX
