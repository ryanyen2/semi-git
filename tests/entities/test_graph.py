"""U2 — entity graph assembly: a name refers to the definition the language binds it to."""

from __future__ import annotations

from ugh.entities.graph import build_entity_graph


def _edges(g, type_="uses"):
    return {(e.src, e.dst) for e in g.edges if e.type == type_}


def test_same_file_use_and_containment():
    src = (
        "def callee():\n"
        "    return 1\n"
        "def caller():\n"
        "    return callee()\n"
        "class C:\n"
        "    def meth(self):\n"
        "        return 2\n"
    )
    g = build_entity_graph({"m.py": src})
    assert ("m.py::caller", "m.py::callee") in _edges(g)
    assert ("m.py::C", "m.py::C.meth") in _edges(g, "contains")


def test_cross_file_use_goes_through_the_import():
    cb = {
        "util.py": "def helper():\n    return 1\n",
        "main.py": "from util import helper\n\ndef run():\n    return helper()\n",
    }
    assert _edges(build_entity_graph(cb)) == {
        ("main.py::run", "util.py::helper"),
        ("main.py::run", "main.py::__import__::util"),
        ("main.py::__import__::util", "util.py::helper"),
    }


def test_unresolved_reference_produces_no_edge():
    g = build_entity_graph({"m.py": "def f():\n    return undefined_thing()\n"})
    assert _edges(g) == set()


def test_constant_used_across_files_through_from_import():
    cb = {
        "app/config.py": "TITLE = 'Counts'\nEVENT_DAYS = {1: 'a'}\n",
        "app/page.py": "from app.config import TITLE\n\ndef render():\n    return TITLE\n",
    }
    edges = _edges(build_entity_graph(cb))
    assert ("app/page.py::render", "app/config.py::TITLE") in edges
    assert not any(dst == "app/config.py::EVENT_DAYS" for _, dst in edges)


def test_module_alias_attribute_resolves_in_the_imported_file():
    cb = {
        "pkg/__init__.py": "",
        "pkg/rank.py": "WEIGHT = 2\ndef score():\n    return WEIGHT\n",
        "main.py": "import pkg.rank as r\nfrom pkg import rank\n\ndef a():\n    return r.score()\n\ndef b():\n    return rank.WEIGHT\n",
    }
    edges = _edges(build_entity_graph(cb))
    assert ("main.py::a", "pkg/rank.py::score") in edges
    assert ("main.py::b", "pkg/rank.py::WEIGHT") in edges
    assert ("pkg/rank.py::score", "pkg/rank.py::WEIGHT") in edges


def test_absolute_imports_resolve_under_each_import_root():
    cb = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": "def f():\n    return 1\n",
        "src/pkg/cli.py": "from pkg.core import f\n\ndef main():\n    return f()\n",
        "tests/test_cli.py": "from pkg.cli import main\n\ndef test_main():\n    assert main() == 1\n",
    }
    edges = _edges(build_entity_graph(cb))
    assert ("src/pkg/cli.py::main", "src/pkg/core.py::f") in edges
    assert ("tests/test_cli.py::test_main", "src/pkg/cli.py::main") in edges

    # Two roots that both hold a `pkg.core`: the module is ambiguous, so no edge.
    cb["lib/pkg/__init__.py"] = ""
    cb["lib/pkg/core.py"] = "def f():\n    return 2\n"
    edges = _edges(build_entity_graph(cb))
    assert not any(dst.endswith("core.py::f") for src, dst in edges if src.startswith("src/pkg/cli.py"))


def test_a_local_name_shadows_the_module_level_one():
    src = (
        "LIMIT = 3\n"
        "def f(LIMIT):\n    return LIMIT\n"
        "def g():\n    LIMIT = 5\n    return LIMIT\n"
        "def h():\n    return [LIMIT for LIMIT in range(2)]\n"
        "def k():\n    return LIMIT\n"
    )
    assert _edges(build_entity_graph({"m.py": src})) == {("m.py::k", "m.py::LIMIT")}


def test_a_method_call_on_an_unknown_object_is_not_resolved():
    cb = {
        "a.py": "def save():\n    return 1\n",
        "b.py": "def run(store):\n    return store.save()\n",
        "c.py": "class Box:\n    def put(self):\n        return self.take()\n    def take(self):\n        return 1\n",
    }
    assert _edges(build_entity_graph(cb)) == {("c.py::Box.put", "c.py::Box.take")}


def test_an_import_statement_stands_on_what_it_imports():
    cb = {
        "lib.py": "def used():\n    return 1\ndef unused_here():\n    return 2\n",
        "main.py": "from lib import used, unused_here\n",
    }
    assert _edges(build_entity_graph(cb)) == {
        ("main.py::__import__::lib", "lib.py::used"),
        ("main.py::__import__::lib", "lib.py::unused_here"),
    }


def test_typescript_named_default_and_namespace_imports():
    cb = {
        "src/m.ts": "export const K = 1;\nexport type T = number;\nexport default function Foo() { return K }\n",
        "src/app.ts": (
            "import Foo, { K as KK } from './m';\n"
            "import * as m from './m';\n"
            "export function a() { return KK }\n"
            "export function b() { return Foo() }\n"
            "export function c(x: m.T) { return x }\n"
        ),
    }
    edges = _edges(build_entity_graph(cb))
    assert ("src/app.ts::a", "src/m.ts::K") in edges
    assert ("src/app.ts::b", "src/m.ts::Foo") in edges
    assert ("src/app.ts::c", "src/m.ts::T") in edges
    assert ("src/app.ts::a", "src/app.ts::__import__::./m") in edges
    assert ("src/app.ts::c", "src/app.ts::__import__::./m#2") in edges
    assert ("src/app.ts::__import__::./m", "src/m.ts::K") in edges
    assert ("src/app.ts::__import__::./m", "src/m.ts::Foo") in edges
    assert ("src/m.ts::Foo", "src/m.ts::K") in edges


def test_jsx_component_is_a_use_and_a_host_tag_is_not():
    cb = {
        "ui/badge.tsx": "export function Badge() { return <span /> }\n",
        "ui/app.tsx": "import { Badge } from './badge';\nexport function App() { return <div><Badge /></div> }\n",
    }
    edges = _edges(build_entity_graph(cb))
    assert ("ui/app.tsx::App", "ui/badge.tsx::Badge") in edges
    assert not any(src == "ui/badge.tsx::Badge" for src, _ in edges)
