"""The U21/D6 birth-id migration: re-mint legacy sequential `F<n>` feature ids to content-addressed
`f-<founding op>` ids, atomically rewriting the pin references that name them, recording old->new in
the committed alias G-Set. Covers dry-run, idempotence, atomicity, cross-vintage sync (a
pre-migration clone reads a post-migration one's re-mint via the alias), and the alias-merge rule
for a genuine two-clone birth-id collision.

Legacy state is synthesized by building a real tree (which now mints modern `f-` ids) and relabeling
its leaves back to `F<n>` -- the exact shape a pre-U21 repo committed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sgt.core import lens, sync
from sgt.lens import reconcile, tree
from sgt.lens.map import build_map
from sgt.lens.pins import Pins, load_pins, save_pins
from sgt.store.gitbind import GitBinding
from tests.laws import corpus


def _seed_legacy(repo: Path, label_prefix: str = "Legacy") -> dict:
    """Build a real tree, then rename every leaf to a legacy `F<n>` id and pin its members + a label
    to it -- the pre-U21 on-disk shape the migration upgrades."""
    result = build_map(repo)
    leaves = sorted(nid for nid, nd in result["nodes"].items() if not nd["children"])
    remap = {nid: f"F{i}" for i, nid in enumerate(leaves)}
    tree._apply_id_map(result, remap)
    tree.save(repo, result)
    nodes = result["nodes"]
    assign = {m: fid for fid in remap.values() for m in nodes[fid]["members"]}
    labels = {fid: f"{label_prefix} {fid}" for fid in remap.values()}
    save_pins(repo, Pins(assign=assign, labels=labels))
    return result


def _legacy_leaf_ids(repo: Path) -> list[str]:
    result = tree.load(repo)
    return sorted(nid for nid, nd in result["nodes"].items() if not nd["children"] and tree._is_legacy_id(nid))


def test_dry_run_reports_the_remap_without_writing(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    lens.get(repo)
    _seed_legacy(repo)
    before_tree = tree.load(repo)
    before_pins = load_pins(repo)

    report = reconcile.migrate_feature_ids(repo, dry_run=True)

    assert report.remap and all(old.startswith("F") for old in report.remap)
    assert all(new.startswith("f-") for new in report.remap.values())
    assert tree.load(repo) == before_tree  # nothing written
    assert load_pins(repo).assign == before_pins.assign
    assert reconcile.load_aliases(repo) == frozenset()


def test_apply_rewrites_tree_pins_and_aliases_atomically(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    lens.get(repo)
    _seed_legacy(repo)
    legacy_ids = _legacy_leaf_ids(repo)

    report = reconcile.migrate_feature_ids(repo)
    assert report.changed and set(report.remap) == set(legacy_ids)

    # tree: no legacy id survives, every new id is the content-addressed form.
    assert _legacy_leaf_ids(repo) == []
    leaves = [nid for nid, nd in tree.load(repo)["nodes"].items() if not nd["children"]]
    assert all(nid.startswith("f-") for nid in leaves)

    # pins: assign values and label keys moved together -- no pin left keyed to a vanished id.
    pins = load_pins(repo)
    assert set(pins.assign.values()) <= set(report.remap.values())
    assert set(pins.labels) <= set(report.remap.values())

    # aliases: every old->new recorded, and resolves back.
    aliases = reconcile.load_aliases(repo)
    assert aliases == frozenset(report.remap.items())
    for old, new in report.remap.items():
        assert reconcile.resolve_alias(aliases, old) == new


def test_migration_is_idempotent(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    lens.get(repo)
    _seed_legacy(repo)

    first = reconcile.migrate_feature_ids(repo)
    assert first.changed
    tree_after_first = tree.load(repo)

    second = reconcile.migrate_feature_ids(repo)
    assert second.remap == {}  # nothing left to re-mint
    assert not second.changed
    assert tree.load(repo) == tree_after_first  # a second run touches nothing


def test_resolve_feature_follows_alias_for_an_old_id(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    lens.get(repo)
    _seed_legacy(repo)
    old_id = _legacy_leaf_ids(repo)[0]

    report = reconcile.migrate_feature_ids(repo)
    new_id = report.remap[old_id]

    from sgt.lens import verbs as lens_verbs
    resolved = lens_verbs.resolve_feature(repo, old_id)  # a stale reference to the pre-migration id
    assert resolved is not None
    assert resolved[1] == new_id  # resolves through the alias to the re-minted feature


# --- two-clone cross-vintage sync + alias-merge collision --------------------------------------


def _init_bare(root: Path) -> Path:
    remote = root / "remote.git"
    remote.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    return remote


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True)
    GitBinding(dest).init()
    return dest


def _push(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True, capture_output=True)


_BASE = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"


def _commit(repo: Path, content: str, msg: str) -> None:
    (repo / "main.py").write_text(content, encoding="utf-8")
    GitBinding(repo).commit_all(msg)
    ideal = lens.get(repo)
    put_sha = lens.put(repo, ideal, message=f"sgt: {msg}")
    lens.record_ideal(repo, ideal, put_sha)


def test_pre_migration_clone_syncs_with_a_post_migration_clone(tmp_path):
    """A clone that migrated (tree carries `f-` ids + an alias G-Set) syncs with one that never
    migrated: the alias travels, so the old-id reference resolves on the synced side (C1/D6)."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _commit(a, _BASE, "init")
    _push(a)
    b = _clone(remote, tmp_path / "b")
    lens.get(b)

    # a migrates a legacy tree and publishes it.
    _seed_legacy(a)
    old_id = _legacy_leaf_ids(a)[0]
    report = reconcile.migrate_feature_ids(a)
    new_id = report.remap[old_id]
    GitBinding(a).commit_all("a: migrate feature ids")
    _push(a)

    # b never migrated; it syncs a's migrated metadata.
    sync.sync(b, remote="origin", branch="main")

    aliases_b = reconcile.load_aliases(b)
    assert (old_id, new_id) in aliases_b  # the re-mint travelled
    assert reconcile.resolve_alias(aliases_b, old_id) == new_id  # and resolves on b's side


def test_divergent_clones_migrate_then_collide_and_resolve_via_alias_merge(tmp_path):
    """Two clones curate divergently, each migrating the *same* local legacy id `F0` to a *different*
    content id (different founding ops), then sync. The alias G-Set unions to a genuine collision;
    the alias-merge rule elects one deterministic winner on *both* clones, and every reference (the
    old id and either minted new id) resolves to that single canonical feature (D6)."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _commit(a, _BASE, "init")
    _push(a)
    b = _clone(remote, tmp_path / "b")
    lens.get(b)

    # divergent unsynced curation: a and b each rework a *different* symbol, so their op stores (and
    # hence any leaf's founding op) differ before they ever sync.
    _commit(a, _BASE.replace("return 1", "return 100"), "a: rework foo")
    _commit(b, _BASE.replace("return 2", "return 200"), "b: rework bar")

    a_map = _seed_legacy(a)
    b_map = _seed_legacy(b)
    a_new = reconcile.migrate_feature_ids(a).remap["F0"]
    b_new = reconcile.migrate_feature_ids(b).remap["F0"]
    assert a_new != b_new  # genuinely different birth ids for the locally-same F0

    GitBinding(a).commit_all("a: migrate")
    _push(a)
    GitBinding(b).commit_all("b: migrate")
    sync.sync(b, remote="origin", branch="main")  # b unions a's aliases -> collision

    aliases = reconcile.load_aliases(b)
    winner = reconcile.resolve_alias(aliases, "F0")
    assert winner in (a_new, b_new)
    # every path to the feature resolves to the one winner -- no split identity survives.
    assert reconcile.resolve_alias(aliases, a_new) == winner
    assert reconcile.resolve_alias(aliases, b_new) == winner
