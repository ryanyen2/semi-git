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


def _seed(tmp_path):
    main(["init", str(tmp_path)])
    proj = Project.open(tmp_path)
    proj.add_feature(Node(id="feat", kind=NodeKind.CAPABILITY, intent="a feature"),
                     [Effect.add_def("m.py", "feat", "def feat():\n    return 1")])
    proj.save()
    return proj


def _in(tmp_path, argv):
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def test_graph_json_is_machine_readable(tmp_path, capsys):
    import json
    _seed(tmp_path)
    capsys.readouterr()  # drain init output
    assert _in(tmp_path, ["graph", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1 and payload["nodes"][0]["id"] == "feat"


def test_blame_json_maps_lines_to_nodes(tmp_path, capsys):
    import json
    _seed(tmp_path)
    capsys.readouterr()  # drain init output
    assert _in(tmp_path, ["blame", "--json", "m.py"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["file"] == "m.py"
    assert any(s["node_id"] == "feat" for s in payload["spans"])


def test_export_dumps_graph(tmp_path, capsys):
    import json
    _seed(tmp_path)
    capsys.readouterr()  # drain init output
    assert _in(tmp_path, ["export"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["nodes"][0]["effects"][0]["op"] == "add_def"


def test_blame_human_readable(tmp_path, capsys):
    _seed(tmp_path)
    assert _in(tmp_path, ["blame", "m.py"]) == 0
    assert "semantic blame" in capsys.readouterr().out


def test_help_mentions_new_verbs(capsys):
    main(["help"])
    out = capsys.readouterr().out
    assert "blame" in out and "export" in out and "--json" in out
