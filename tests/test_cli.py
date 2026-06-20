"""CLI surface tests (graph-only): read-only verbs, report rendering, quarantine visibility.

The graph-reasoning `plan` path is covered live; here we test the pure CLI surface without
any LLM or backend.
"""

from sgt.cli import main
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


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
