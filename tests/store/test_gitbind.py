"""U1 tests: git binding — init, commit mapping, trailer identity, orphan detection."""

from sgt.store.gitbind import (
    GitBinding,
    _CatFileBatch,
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


def test_history_backward_mirrors_history_reversed(tmp_path):
    gb, _ = init_store(tmp_path)
    shas = []
    for i in range(5):
        (tmp_path / "f.txt").write_text(f"v{i}", encoding="utf-8")
        shas.append(gb.commit_all(f"feat: v{i}"))

    forward = gb.history(since=None, target=shas[-1])
    backward = gb.history_backward(tip=shas[-1])
    assert backward == list(reversed(forward))


def test_history_backward_root_commit_has_no_parent(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "f.txt").write_text("v1", encoding="utf-8")
    sha1 = gb.commit_all("feat: v1")
    (tmp_path / "f.txt").write_text("v2", encoding="utf-8")
    gb.commit_all("feat: v2")

    rows = gb.history_backward(tip=sha1)
    assert len(rows) == 1
    assert rows[0][0] == sha1
    assert rows[0][1] is None


def test_history_backward_limit_caps_walk_to_newest_rows(tmp_path):
    gb, _ = init_store(tmp_path)
    shas = []
    for i in range(5):
        (tmp_path / "f.txt").write_text(f"v{i}", encoding="utf-8")
        shas.append(gb.commit_all(f"feat: v{i}"))

    full = gb.history_backward(tip=shas[-1])
    limited = gb.history_backward(tip=shas[-1], limit=2)
    assert limited == full[:2]
    assert len(limited) == 2


def test_blob_bytes_batch_and_argv_paths_agree(tmp_path):
    """The persistent `cat-file --batch` fast path and the one-shot `git show` argv fallback must
    return byte-identical content for the same blob, including binary with NULs and high bytes --
    pins that the batch optimization never diverges from a plain read."""
    gb, _ = init_store(tmp_path)
    raw = bytes(range(256)) * 4
    (tmp_path / "blob.bin").write_bytes(raw)
    sha = gb.commit_all("feat: binary blob")

    assert gb.blob_bytes(sha, "blob.bin") == raw  # persistent batch pipe
    assert gb._show_blob(sha, "blob.bin") == raw  # one-shot argv fallback
    assert gb.blob_bytes(sha, "blob.bin") == gb._show_blob(sha, "blob.bin")


def test_blob_bytes_newline_path_uses_argv_and_keeps_batch_stream_aligned(tmp_path):
    """A path with a newline can't be framed as a `<rev>:<path>` line in the shared `cat-file
    --batch` pipe -- feeding it through would split into two requests and desync the stream for
    every later reader in the process. It detours through a one-shot argv read, and a normal
    batched read afterward still returns its own content (stream intact), including within one
    mixed `blob_bytes_many` call that stays aligned spec-for-spec."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "normal.txt").write_bytes(b"normal-content")
    (tmp_path / "we\nird.txt").write_bytes(b"newline-path-content")
    sha = gb.commit_all("feat: add a newline-named file")

    assert gb.blob_bytes(sha, "we\nird.txt") == b"newline-path-content"  # argv fallback
    assert gb.blob_bytes(sha, "normal.txt") == b"normal-content"  # pipe still aligned
    assert gb.blob_bytes_many([
        (sha, "normal.txt"), (sha, "we\nird.txt"), (sha, "normal.txt"),
    ]) == [b"normal-content", b"newline-path-content", b"normal-content"]


def test_blob_oid_reads_gitlink_oid_from_tree_when_batch_check_reports_missing(tmp_path):
    """A gitlink (submodule, mode 160000) or promisor-filtered blob is not in this repo's object
    store, so `cat-file --batch-check` reports it missing -- but the tree still records its oid.
    `blob_oid` falls back to `ls-tree`, which reads the oid straight from the tree object, so the
    entry keeps its stable content address instead of folding to a None (unchained) whole-file
    version and silently breaking chain continuity across the commit."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "f.txt").write_text("hi", encoding="utf-8")
    gb.commit_all("feat: base")

    gitlink_oid = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"  # a commit sha absent from this odb
    gb._git("update-index", "--add", "--cacheinfo", f"160000,{gitlink_oid},sub")
    gb._git("commit", "-q", "-m", "feat: add gitlink sub")  # not commit_all: `add -A` would drop it
    sha = gb.head()

    assert sha is not None
    assert gb.blob_oid(sha, "sub") == gitlink_oid  # from ls-tree, not the missing batch-check


def test_cat_file_batch_restarts_after_its_process_dies(tmp_path):
    """The persistent `cat-file --batch` process is a long-lived optimization; if it dies (objects
    moved underneath us, an OOM kill, a crash) the next read must transparently restart it rather
    than propagate a broken-pipe error. Reliability contract: a read never fails because the pooled
    process went away."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "f.txt").write_bytes(b"content")
    sha = gb.commit_all("feat: add f")

    batch = _CatFileBatch(str(tmp_path))
    assert batch.read_many([(sha, "f.txt")]) == [b"content"]  # starts the process
    batch.proc.kill()  # simulate the process dying underneath us
    batch.proc.wait()
    assert batch.proc.poll() is not None  # confirmed dead

    assert batch.read_many([(sha, "f.txt")]) == [b"content"]  # transparently restarted
    batch.close()


def test_commits_touching_follows_a_side_branch_deletion_across_a_merge(tmp_path):
    """`commits_touching` is scoped to a single path deliberately: a union pathspec over several
    paths is NOT a sound superset of each path's own walk, because git's history simplification
    follows only one TREESAME parent at a merge and a wider pathspec can flip which parent that is
    -- rerouting the walk away from the side branch that closed a symbol (the miner's rebirth
    lookback, U9, would then mis-mint a fresh birth instead of chaining). This pins the property
    the per-path walk guarantees: the side-branch commit that emptied `a.py` is reachable from the
    post-merge tip's own-path history."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    c0 = gb.commit_all("feat: add foo")
    main = gb._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    gb._git("checkout", "-q", "-b", "side")
    (tmp_path / "a.py").write_text("", encoding="utf-8")  # close foo on the side branch
    closing = gb.commit_all("chore: empty a.py")

    gb._git("checkout", "-q", main)
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("feat: add bar (unrelated, on main)")

    gb._git("merge", "-q", "--no-ff", "-m", "merge side", "side")
    tip = gb.head()
    assert tip is not None

    shas = [sha for sha, _parent in gb.commits_touching(tip, "a.py")]
    assert closing in shas  # the side-branch deletion is on a.py's own-path history
    assert c0 in shas  # as is the original add


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


def test_batch_reads_do_not_alias_across_chdir_with_relative_repo(tmp_path, monkeypatch):
    """The persistent cat-file batch registry keys on the RESOLVED repo path: `GitBinding(".")`
    under two different cwds must serve each repo's own blobs -- a relative key would hand one
    repo's batch process to the other repo's reads after a chdir (tests chdir constantly)."""
    repo_a, repo_b = tmp_path / "a", tmp_path / "b"
    for repo, content in ((repo_a, b"alpha\n"), (repo_b, b"beta\n")):
        gb, _ = init_store(repo)
        (repo / "f.txt").write_bytes(content)
        gb.commit_all("add f")
    monkeypatch.chdir(repo_a)
    assert GitBinding(".").blob_bytes("HEAD", "f.txt") == b"alpha\n"
    monkeypatch.chdir(repo_b)
    assert GitBinding(".").blob_bytes("HEAD", "f.txt") == b"beta\n"
    monkeypatch.chdir(repo_a)  # back again: repo A's process, not a stale B handle
    assert GitBinding(".").blob_bytes("HEAD", "f.txt") == b"alpha\n"
