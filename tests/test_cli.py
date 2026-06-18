"""U8 tests: CLI checkpoint prompt, quarantine visibility, and read-only verbs.

The LLM-backed `do` path is covered live by scripts/e2e_fanout.py; here we test the
pure CLI surface (confirm prompt, report rendering, graph/show) without a backend.
"""

import builtins

from sgt.cli import confirm_plan, main
from sgt.effects.model import Effect
from sgt.orchestrate.constraint import ConstraintGraph, SubTask
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


def _plan():
    g = ConstraintGraph()
    g.add(SubTask("a", "make a", provides=["a"]))
    g.add(SubTask("b", "use a", needs=["a"]))
    return g


def test_confirm_plan_yes(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", lambda *a: "y")
    assert confirm_plan(_plan()) is True
    out = capsys.readouterr().out
    assert "Proposed fan-out plan" in out and "layer 1" in out and "layer 2" in out


def test_confirm_plan_no(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "")
    assert confirm_plan(_plan()) is False


def test_confirm_plan_eof_is_no(monkeypatch):
    def raise_eof(*a):
        raise EOFError
    monkeypatch.setattr(builtins, "input", raise_eof)
    assert confirm_plan(_plan()) is False


def test_init_and_graph_roundtrip(tmp_path, capsys):
    assert main(["init", str(tmp_path)]) == 0
    # add a node directly, then render the graph from a fresh process-like call
    proj = Project.open(tmp_path)
    proj.add_feature(Node(id="feat", kind=NodeKind.CAPABILITY, intent="a feature"),
                     [Effect.add_def("m.py", "feat", "def feat():\n    return 1")])
    proj.save()
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert main(["graph"]) == 0
    finally:
        os.chdir(cwd)
    assert "feat" in capsys.readouterr().out


def test_graph_shows_quarantine_witness(tmp_path, capsys):
    main(["init", str(tmp_path)])
    proj = Project.open(tmp_path)
    proj.add_feature(Node(id="base", kind=NodeKind.CAPABILITY, intent="base"),
                     [Effect.add_def("m.py", "base", "def base():\n    return 1")])
    proj.quarantine(Node(id="q1", kind=NodeKind.CAPABILITY, intent="held"),
                    [Effect.add_def("m.py", "base", "def base():\n    return 2")],
                    reason="non_commuting_with:base", held_descs=["add_def base (m.py)"],
                    against_ids=["base"])
    proj.save()
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        main(["graph"])
        main(["show", "q1"])
    finally:
        os.chdir(cwd)
    out = capsys.readouterr().out
    assert "quarantined" in out and "non_commuting_with:base" in out


def test_help_mentions_yes_flag(capsys):
    main(["help"])
    assert "--yes" in capsys.readouterr().out
