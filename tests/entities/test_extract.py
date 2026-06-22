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
