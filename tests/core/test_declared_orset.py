"""The declared-edge OR-Set (U21/D6): `sgt after` adds an edge with a unique tag, `sgt after
--retract` tombstones the tags it observes, and the *live* edge set (what `order` consumes) is
every edge value with a surviving tag. Unlike the pre-U21 flat G-Set, a retraction is durable,
travelling state -- and a concurrent add elsewhere survives it.

`_load_declared` (the consumer-facing live set) and the legacy `.sgt/declared.json` dual-write are
both exercised so the behavior-preserving integration with `order`/`sync` is pinned.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sgt.core import lens, sync
from sgt.core.lens import (
    DeclaredORSet,
    _load_declared,
    _save_declared,
    declare_after,
    load_declared_orset,
    retract_after,
    save_declared_orset,
)
from sgt.core.store import Store
from sgt.store.gitbind import GitBinding


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".sgt").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_declare_after_makes_an_edge_live(tmp_path):
    repo = _repo(tmp_path)
    declare_after(repo, "opA", "opB")
    assert _load_declared(repo) == frozenset({("opA", "opB")})
    # each add carries a distinct tag -- two declares of the same value are two tags, one live edge.
    declare_after(repo, "opA", "opB")
    orset = load_declared_orset(repo)
    assert len(orset.adds) == 2
    assert orset.live() == frozenset({("opA", "opB")})


def test_retract_tombstones_locally_observed_tags(tmp_path):
    repo = _repo(tmp_path)
    declare_after(repo, "opA", "opB")
    declare_after(repo, "opC", "opD")
    tombstoned = retract_after(repo, "opA", "opB")
    assert len(tombstoned) == 1
    assert _load_declared(repo) == frozenset({("opC", "opD")})  # only the retracted edge is gone


def test_concurrent_add_survives_a_retraction(tmp_path):
    """OR-Set's defining property (why not a plain delete): retracting the tags one clone observes
    must not kill a concurrent add elsewhere carrying a tag that clone never saw."""
    repo = _repo(tmp_path)
    # ours: one observed add of the edge.
    declare_after(repo, "opA", "opB")
    ours = load_declared_orset(repo)
    # theirs: an independent add of the *same value* with a tag we haven't seen.
    theirs = DeclaredORSet(adds=frozenset({("opA", "opB", "theirs-tag")}))
    # we retract every tag we locally observe, then the two OR-Sets merge (sync).
    retract_after(repo, "opA", "opB")
    merged = load_declared_orset(repo).union(theirs)
    assert merged.live() == frozenset({("opA", "opB")})  # theirs' unseen tag keeps the edge alive


def test_retraction_travels_when_the_tag_is_shared(tmp_path):
    """The mirror of the previous test: a retraction of a tag *both* sides hold does remove the edge
    on merge -- so a resolved retraction stays resolved (a shared legacy edge, deterministically
    tagged, propagates its removal)."""
    shared = DeclaredORSet(adds=frozenset({("opA", "opB", "shared")}))
    retracted = DeclaredORSet(adds=frozenset({("opA", "opB", "shared")}), tombstones=frozenset({"shared"}))
    merged = shared.union(retracted)
    assert merged.live() == frozenset()


def test_union_is_order_independent(tmp_path):
    a = DeclaredORSet(adds=frozenset({("x", "y", "t1")}), tombstones=frozenset({"t0"}))
    b = DeclaredORSet(adds=frozenset({("x", "y", "t2"), ("p", "q", "t3")}), tombstones=frozenset({"t2"}))
    assert a.union(b) == b.union(a)  # commutative merge (LAW-U)
    # (x,y) stays live via t1 even though t2 is tombstoned; (p,q) is live via t3.
    assert a.union(b).live() == frozenset({("x", "y"), ("p", "q")})


def test_legacy_flat_gset_is_lifted_and_dual_written(tmp_path):
    repo = _repo(tmp_path)
    # a pre-U21 repo has only the flat committed G-Set, no OR-Set file yet.
    _save_declared(repo, frozenset({("opA", "opB"), ("opC", "opD")}))
    assert not (repo / ".sgt" / "declared_edges.json").exists()

    live = _load_declared(repo)  # reads the OR-Set, lifting the flat G-Set on the fly
    assert live == frozenset({("opA", "opB"), ("opC", "opD")})

    # once anything writes the OR-Set, the legacy path is dual-written so old readers still see edges.
    save_declared_orset(repo, load_declared_orset(repo))
    assert (repo / ".sgt" / "declared_edges.json").exists()
    from sgt import state
    assert frozenset(tuple(p) for p in state.load_json(repo, "declared")) == live


# --- two-clone: a declared edge travels by tag through a real sync ------------------------------


_BASE = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"


def _two_clones(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    remote.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    a = tmp_path / "a"
    subprocess.run(["git", "clone", "-q", str(remote), str(a)], check=True, capture_output=True)
    GitBinding(a).init()
    lens.init(a)
    (a / "main.py").write_text(_BASE, encoding="utf-8")
    GitBinding(a).commit_all("init")
    ideal = lens.get(a)
    put_sha = lens.put(a, ideal, message="sgt: init")
    lens.record_ideal(a, ideal, put_sha)
    subprocess.run(["git", "-C", str(a), "push", "-q", "origin", "main"], check=True, capture_output=True)
    b = tmp_path / "b"
    subprocess.run(["git", "clone", "-q", str(remote), str(b)], check=True, capture_output=True)
    GitBinding(b).init()
    lens.get(b)
    return a, b


def test_declared_edge_travels_through_sync(tmp_path):
    """`sgt after` on one clone, committed and pushed, unions into the other clone's OR-Set on sync
    (by tag) -- so the live declared edge appears on the syncing side without any textual merge."""
    a, b = _two_clones(tmp_path)
    ops = sorted(op.id for op in Store(a).all_ops())
    edge = (ops[0], ops[1])

    declare_after(a, *edge)
    GitBinding(a).commit_all("a: declare order edge")
    subprocess.run(["git", "-C", str(a), "push", "-q", "origin", "main"], check=True, capture_output=True)

    assert edge not in _load_declared(b)  # not yet
    sync.sync(b, remote="origin", branch="main")
    assert edge in _load_declared(b)  # the OR-Set add travelled and is live on b
