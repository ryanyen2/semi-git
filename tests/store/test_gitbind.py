"""U1 tests: git binding — init, commit mapping, trailer identity, orphan detection."""

from sgt.store.gitbind import (
    GitBinding,
    format_trailer,
    init_store,
    known_commit_ids,
    new_node_id,
    parse_node_id,
)
from sgt.store.graph import Node, NodeKind, SemanticGraph


def test_trailer_roundtrip():
    nid = new_node_id()
    msg = f"feat: add redirect\n\n{format_trailer(nid)}"
    assert parse_node_id(msg) == nid


def test_parse_node_id_absent():
    assert parse_node_id("just a message, no trailer") is None


def test_init_store_creates_repo_and_graph(tmp_path):
    gb, graph_path = init_store(tmp_path)
    assert gb.is_repo()
    assert graph_path.exists()
    # the persisted graph loads as an empty graph
    assert SemanticGraph.load(graph_path).nodes() == []


def test_commit_shas_empty_then_one(tmp_path):
    gb, _ = init_store(tmp_path)
    assert gb.commit_shas() == []  # no commits yet
    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    sha = gb.commit_all("feat: add f")
    assert gb.commit_shas() == [sha]


def test_node_id_stable_across_amend(tmp_path):
    gb, _ = init_store(tmp_path)
    nid = new_node_id()
    (tmp_path / "f.txt").write_text("v1", encoding="utf-8")
    sha1 = gb.commit_all("feat: add f", node_id=nid)
    assert gb.node_id_for_commit(sha1) == nid

    # amend changes the SHA but preserves the message (and the trailer)
    (tmp_path / "f.txt").write_text("v2", encoding="utf-8")
    gb.stage_all()
    sha2 = gb.amend_no_edit()
    assert sha2 != sha1
    assert gb.node_id_for_commit(sha2) == nid  # identity survives the rewrite


def test_detect_orphans_flags_out_of_band_commit(tmp_path):
    gb, graph_path = init_store(tmp_path)
    # a commit semi-git knows about (mapped to a node)
    nid = new_node_id()
    (tmp_path / "tracked.txt").write_text("x", encoding="utf-8")
    known_sha = gb.commit_all("feat: tracked", node_id=nid)

    g = SemanticGraph()
    g.add_node(
        Node(id=nid, kind=NodeKind.CAPABILITY, intent="tracked", commit_ids=[known_sha])
    )

    # a commit made directly via git, outside sgt
    (tmp_path / "rogue.txt").write_text("y", encoding="utf-8")
    orphan_sha = gb.commit_all("chore: out-of-band edit")

    orphans = gb.detect_orphans(known_commit_ids(g))
    assert orphan_sha in orphans
    assert known_sha not in orphans
