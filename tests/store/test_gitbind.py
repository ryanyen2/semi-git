"""U1 tests: git binding — init, commit mapping, trailer identity, orphan detection."""

from sgt.store.gitbind import (
    GitBinding,
    format_trailer,
    init_store,
    new_node_id,
    parse_node_id,
)


def test_trailer_roundtrip():
    nid = new_node_id()
    msg = f"feat: add redirect\n\n{format_trailer(nid)}"
    assert parse_node_id(msg) == nid


def test_parse_node_id_absent():
    assert parse_node_id("just a message, no trailer") is None


def test_init_store_creates_repo_and_sgt_dir(tmp_path):
    gb, sgt_dir = init_store(tmp_path)
    assert gb.is_repo()
    assert sgt_dir.is_dir()


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


def test_blob_bytes_reads_binary_content(tmp_path):
    gb, _ = init_store(tmp_path)
    raw = bytes([0x89, 0x50, 0x4E, 0x47, 0x00, 0x01, 0x02])
    (tmp_path / "logo.bin").write_bytes(raw)
    sha = gb.commit_all("feat: add binary")
    assert gb.blob_bytes(sha, "logo.bin") == raw
    assert gb.blob_bytes(sha, "missing.bin") is None


def test_blob_oid_matches_hash_object(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    sha = gb.commit_all("feat: add f")
    expected = gb._git("hash-object", "f.txt").stdout.strip()
    assert gb.blob_oid(sha, "f.txt") == expected
    assert gb.blob_oid(sha, "missing.txt") is None


def test_history_oldest_first_with_parents(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "f.txt").write_text("v1", encoding="utf-8")
    sha1 = gb.commit_all("feat: v1")
    (tmp_path / "f.txt").write_text("v2", encoding="utf-8")
    sha2 = gb.commit_all("feat: v2")

    rows = gb.history()
    assert [sha for sha, _, _ in rows] == [sha1, sha2]
    assert rows[0][1] is None  # root commit has no parent
    assert rows[1][1] == sha1

    since_rows = gb.history(since=sha1)
    assert [sha for sha, _, _ in since_rows] == [sha2]
    assert since_rows[0][1] == sha1  # still diffs against its true predecessor


def test_detect_orphans_flags_out_of_band_commit(tmp_path):
    gb, _ = init_store(tmp_path)
    # a commit sgt knows about
    nid = new_node_id()
    (tmp_path / "tracked.txt").write_text("x", encoding="utf-8")
    known_sha = gb.commit_all("feat: tracked", node_id=nid)

    # a commit made directly via git, outside sgt
    (tmp_path / "rogue.txt").write_text("y", encoding="utf-8")
    orphan_sha = gb.commit_all("chore: out-of-band edit")

    orphans = gb.detect_orphans({known_sha})
    assert orphan_sha in orphans
    assert known_sha not in orphans
