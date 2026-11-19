"""The lens between git and the op store.

`get` mines the commits new to the current ref since its witness, oldest first, stores the ops (an
op mined twice merges its provenance), and returns the ref's ideal: every op mined from the ref's
history, reduced to a valid ideal, plus the working tree's uncommitted edits. Every op comes from
mining a commit, ugh's own included, so `code(I)` is the ref's tree. A first contact mines in
deadline-bounded chunks and the witness records how far it got. `commit` commits the working tree
and mines that commit like any other.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from ugh import state
from ugh.core import opindex, order, tiers
from ugh.core.fold import code
from ugh.core.ideal import Ideal
from ugh.core.mine import mine
from ugh.core.op import MINER_VERSION
from ugh.core.store import Store, locked_section
from ugh.store.gitbind import GitBinding


class DirtyWorkingTreeError(Exception):
    """A verb would overwrite or commit uncommitted working-tree changes."""


_CHUNK_BUDGET_SECONDS = 10.0  # one chunk's wall-clock ceiling, so an interactive read cannot hang

_MERGE_PSEUDO_REFS = (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"))


def merge_in_progress(gb: GitBinding) -> str | None:
    """The in-flight git merge, cherry-pick or revert, or None. Its tree holds conflict markers,
    so the dirty pass skips it and `save` refuses."""
    for pseudo, verb in _MERGE_PSEUDO_REFS:
        if gb.pseudo_ref_set(pseudo):
            return verb
    return None


def _mined_by_this_miner(repo: Path) -> bool:
    """Whether the local witness and ideal tables were written by this miner version. Another
    version's ops are not this version's, so its tables are empty to this one and history is mined
    again from the start."""
    return state.load_json(repo, "mined_by", default=None) == MINER_VERSION


def _load_witnesses(repo: Path) -> dict[str, str]:
    return state.load_json(repo, "witness", default={}) if _mined_by_this_miner(repo) else {}


def _save_witnesses(repo: Path, table: dict[str, str]) -> None:
    state.save_json(repo, "witness", table)


def _load_ideal_table(repo: Path) -> dict[str, list[str]]:
    """The per-ref ideal cached from history: `{ref_key: [sorted op_ids]}`, never the dirty
    overlay."""
    return state.load_json(repo, "ideal_table", default={}) if _mined_by_this_miner(repo) else {}


def _save_ideal_table(repo: Path, table: dict[str, list[str]]) -> None:
    state.save_json(repo, "ideal_table", table)


def _load_sync_cache(repo: Path) -> dict[str, dict]:
    """The per-ref no-op gate, `{ref_key: {"fp", "ids", "store"}}`: when the fingerprint and the
    store digest are unchanged since the last `_sync`, that sync would return the same ideal."""
    return state.load_json(repo, "sync_cache", default={})


def _save_sync_cache(repo: Path, table: dict[str, dict]) -> None:
    state.save_json_if_changed(repo, "sync_cache", table)


def _store_digest(ops) -> str:
    """A digest of the op store's id set. The ideal depends on the whole store, which the
    fingerprint does not cover."""
    h = hashlib.sha256()
    for oid in sorted(op.id for op in ops):
        h.update(oid.encode())
        h.update(b"\x00")
    return h.hexdigest()


# Bump to evict every cached gate entry without re-mining history (bumping MINER_VERSION re-mines).
_FINGERPRINT_SCHEMA = "2"


def _sync_fingerprint(gb: GitBinding, head: str, ideal_entry, digest: str | None = None,
                     ) -> str | None:
    """The no-op gate's key over HEAD, the dirty source, the miner version and the persisted ideal
    entry, or None when git cannot digest the dirty tree (then mine). A writer passes the `digest`
    it sampled before mining, so the entry describes the tree it mined, not the tree after."""
    if digest is None:
        digest = gb.dirty_source_digest()
    if digest is None:
        return None
    h = hashlib.sha256()
    h.update(_FINGERPRINT_SCHEMA.encode())
    h.update(b"\x00")
    h.update(head.encode())
    h.update(b"\x00")
    h.update(digest.encode())
    h.update(b"\x00")
    h.update(MINER_VERSION.encode())
    h.update(b"\x00")
    h.update(json.dumps(ideal_entry, sort_keys=True).encode())
    return h.hexdigest()


def _ref_key(gb: GitBinding) -> str | None:
    """This ref's stable key in the witness table: its symbolic name, or the raw HEAD sha in
    detached-HEAD state (each detached position tracked independently)."""
    return gb.symbolic_ref() or gb.head()


# -- persisted derivation stamps (`.sgt/local/derive_cache.json`) ----------------------------
# Validation and reduction are pure functions of the content-addressed id set, so their results
# persist across processes. Presence (`ids <= present`) is still checked live: a `git switch` can
# remove op files without changing the ids.
_DERIVE_STATE: dict[str, dict] = {}  # repo_key -> {"valid": [...], "valid_set": set, "reduce": {}}
_DERIVE_VALID_MAX = 128
_DERIVE_REDUCE_MAX = 8


def _ids_digest(ids) -> str:
    h = hashlib.sha256()
    for oid in sorted(ids):
        h.update(oid.encode())
        h.update(b"\n")
    return h.hexdigest()


def _derive_state(repo: Path) -> dict:
    rk = os.path.realpath(repo)
    st = _DERIVE_STATE.get(rk)
    if st is None:
        body = state.load_json(repo, "derive_cache", default=None)
        if not isinstance(body, dict):
            body = {}
        st = {
            "valid": [d for d in body.get("valid", []) if isinstance(d, str)],
            "reduce": {
                k: v for k, v in body.get("reduce", {}).items() if isinstance(v, dict)
            },
        }
        st["valid_set"] = set(st["valid"])
        _DERIVE_STATE[rk] = st
    return st


def _derive_record(repo: Path, st: dict) -> None:
    """Trim to the caps, oldest first, and persist. Concurrent writers last-win; a lost entry costs
    one re-derivation."""
    st["valid"] = st["valid"][-_DERIVE_VALID_MAX:]
    st["valid_set"] = set(st["valid"])
    reduce_map = st["reduce"]
    while len(reduce_map) > _DERIVE_REDUCE_MAX:
        del reduce_map[next(iter(reduce_map))]
    state.save_json(repo, "derive_cache", {"valid": st["valid"], "reduce": reduce_map})


def _validated_from_ops(repo: Path, ids, ops: list) -> Ideal:
    """`Ideal.from_ops` with the persisted validity stamp: a stamped digest skips grounding (fixed
    by the content-addressed ids), while presence is checked live either way. An unstamped set
    takes the full check and earns its stamp."""
    ids = frozenset(ids)
    st = _derive_state(repo)
    digest = _ids_digest(ids)
    if digest in st["valid_set"] and ids <= {op.id for op in ops}:
        return Ideal(op_ids=ids)
    ideal = Ideal.from_ops(ids, ops)
    st["valid"].append(digest)
    st["valid_set"].add(digest)
    _derive_record(repo, st)
    return ideal


def _reduced_ideal_ids(repo: Path, raw_ids, ops: list) -> frozenset[str]:
    """`order.reduce_to_ideal` with a persisted stamp of whichever side, kept or dropped, is
    smaller."""
    present = {op.id for op in ops}
    key_set = frozenset(raw_ids) & present
    st = _derive_state(repo)
    digest = _ids_digest(key_set)
    entry = st["reduce"].get(digest)
    if isinstance(entry, dict) and isinstance(entry.get("ids"), list):
        ids = frozenset(entry["ids"])
        return ids if entry.get("side") == "kept" else key_set - ids
    result = order.reduce_to_ideal(key_set, ops)
    dropped = key_set - result
    st["reduce"][digest] = (
        {"side": "kept", "ids": sorted(result)} if len(result) < len(dropped)
        else {"side": "dropped", "ids": sorted(dropped)}
    )
    _derive_record(repo, st)
    return result


def _committed_ids_by_provenance(gb: GitBinding, store: Store) -> set[str]:
    """This ref's ideal derived from history: every op mined from its ancestry, grounded."""
    all_ops = opindex.index_ops(store.repo)
    return set(_reduced_ideal_ids(store.repo, _provenance_ids_at(gb, "HEAD", all_ops), all_ops))


def ops_with_frontier_images(repo: str | Path, ideal: Ideal) -> list:
    """The footprint-only index ops with the full ops (images included) of `ideal`'s frontier
    producers substituted in: enough for `fold.code(ideal, ...)` without decoding every image. Do
    not fold a different ideal from the result; its frontier ops would have empty images."""
    from concurrent.futures import ThreadPoolExecutor

    repo = Path(repo)
    ops = opindex.index_ops(repo)
    tip = ideal.frontier(ops)
    need = sorted(set(tip.values()))
    store = Store(repo)

    def _safe_get(oid: str):
        # A corrupt op file is skipped, as `Store.all_ops` does; a read view must not error.
        try:
            return store.get(oid)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        full = list(pool.map(_safe_get, need))
    by_id = {op.id: op for op in full if op is not None}
    # Drop an unreadable producer rather than keep it imageless, which would fold to empty files.
    unreadable = set(need) - set(by_id)
    return [by_id.get(op.id, op) for op in ops if op.id not in unreadable]


def current_ideal(repo: str | Path) -> Ideal:
    """The current ref's committed ideal as last mined, without mining: the cached table entry, or
    the provenance scan when the ref has none."""
    repo = Path(repo)
    gb = GitBinding(repo)
    store = Store(repo)
    key = _ref_key(gb)
    table = _load_ideal_table(repo)
    ids = frozenset(table[key]) if key is not None and key in table else _committed_ids_by_provenance(gb, store)
    return _validated_from_ops(repo, ids, opindex.index_ops(repo))


def ideal_for_ref(repo: str | Path, ref: str = "HEAD") -> Ideal:
    """The ideal `ref`'s history implies, without mining: this miner's ops whose provenance is in
    `ref`'s ancestry, reduced. A ref never mined yields less; `get()` it first."""
    repo = Path(repo)
    all_ops = opindex.index_ops(repo)
    included = _provenance_ids_at(GitBinding(repo), ref, all_ops)
    return _validated_from_ops(repo, _reduced_ideal_ids(repo, included, all_ops), all_ops)


def _provenance_ids_at(gb: GitBinding, ref: str, all_ops: list) -> set[str]:
    """This miner version's ops whose `provenance` intersects `ref`'s commit ancestry."""
    ref_commits = set(gb.commit_shas(ref))
    return {op.id for op in all_ops
            if op.miner_version == MINER_VERSION and not ref_commits.isdisjoint(op.provenance)}


def _sync(repo: Path, treat_as_root: str | None = None, *, unbounded: bool = False) -> Ideal:
    """Mine what is new to the current ref and return its ideal. `treat_as_root` mines from that
    commit against the empty tree; `unbounded` mines to the end instead of one chunk."""
    gb = GitBinding(repo)
    store = Store(repo)
    store.init()

    head = gb.head()
    if head is None:
        # No commit yet: only the working tree's ops, persisted, with no table keyed on a ref.
        if not gb.has_dirty_source():
            return Ideal.from_ops(frozenset(), [])
        mined_ops, _ = mine(repo, include_dirty=True)
        stored_ops = [store.add(op) for op in mined_ops]
        with locked_section(repo):
            if opindex.is_stale(repo):
                opindex.rebuild(repo, store)
            else:
                opindex.apply_delta(repo, stored_ops)
        all_ops = opindex.index_ops(repo)
        pending_ids = {op.id for op in stored_ops}
        return Ideal.from_ops(order.reduce_to_ideal(pending_ids, all_ops), all_ops)

    key = _ref_key(gb) or head
    prev_head = _load_witnesses(repo).get(key)

    # No-op gate: HEAD, the dirty source, the persisted ideal and the store are all unchanged since
    # the last sync, so it would return the same ideal.
    if treat_as_root is None and prev_head == head:
        ideal_entry = _load_ideal_table(repo).get(key)
        fp = _sync_fingerprint(gb, head, ideal_entry)
        cached = _load_sync_cache(repo).get(key)
        if fp is not None and cached is not None and cached.get("fp") == fp:
            cached_ids = frozenset(cached.get("ids", []))
            index_ops = opindex.index_ops(repo)
            if (cached_ids <= {op.id for op in index_ops}
                    and cached.get("store") == _store_digest(index_ops)):
                return _validated_from_ops(repo, cached_ids, index_ops)

    # Sampled before mining, so an edit landing mid-sync misses the gate next time.
    dirty_digest_at_mine = gb.dirty_source_digest()
    include_dirty = gb.has_dirty_source() and merge_in_progress(gb) is None

    # Oldest first, so every commit's parent is mined before it.
    deadline = None if unbounded or treat_as_root is not None else time.monotonic() + _CHUNK_BUDGET_SECONDS
    if treat_as_root is not None:
        rows = gb.history(gb.parent_of(treat_as_root), head)
    elif prev_head is None:
        known = {sha for op in opindex.index_ops(repo) if op.miner_version == MINER_VERSION
                 for sha in op.provenance}
        rows = [row for row in gb.history(None, head) if row[0] not in known]
    else:
        rows = gb.history(prev_head, head)
    mined_ops, last_sha = mine(repo, treat_as_root=treat_as_root, include_dirty=include_dirty,
                               deadline=deadline, history_override=rows)
    new_witness = head if not rows or last_sha == rows[-1][0] else last_sha or prev_head

    # Judged before our own writes, which always look stale; stale here means another writer.
    index_stale_before_add = opindex.is_stale(repo)
    new_committed_ids: set[str] = set()
    pending_ids: set[str] = set()
    stored_ops = []
    for op in mined_ops:
        stored = store.add(op)
        stored_ops.append(stored)
        (new_committed_ids if stored.provenance else pending_ids).add(stored.id)
    with locked_section(repo):
        if index_stale_before_add:
            opindex.rebuild(repo, store)
        else:
            opindex.apply_delta(repo, stored_ops)

    all_ops = opindex.index_ops(repo)
    ideal_table = _load_ideal_table(repo)
    already_seeded = key in ideal_table
    base_ids = set(ideal_table.get(key, ()))
    seed = _provenance_ids_at(gb, "HEAD", all_ops)
    committed_ids = set(_reduced_ideal_ids(repo, seed, all_ops))

    ideal_changed = not already_seeded or committed_ids != base_ids
    witness_changed = new_witness != prev_head
    if ideal_changed or witness_changed:
        with locked_section(repo):
            state.save_json(repo, "mined_by", MINER_VERSION)
            if ideal_changed:
                ideal_table[key] = sorted(committed_ids)
                _save_ideal_table(repo, ideal_table)
            if witness_changed:
                table = _load_witnesses(repo)
                table[key] = new_witness
                _save_witnesses(repo, table)

    # The returned ideal adds the uncommitted edits to the committed set.
    result = Ideal.from_ops(order.reduce_to_ideal(committed_ids | pending_ids, all_ops), all_ops)

    if treat_as_root is None and new_witness == head:
        fp = _sync_fingerprint(gb, head, sorted(committed_ids), digest=dirty_digest_at_mine)
        if fp is not None:
            table = _load_sync_cache(repo)
            table[key] = {"fp": fp, "ids": sorted(result.op_ids), "store": _store_digest(all_ops)}
            _save_sync_cache(repo, table)
    return result


def get(repo: str | Path, *, unbounded: bool = False) -> Ideal:
    """Mine what is new to the current ref and return its ideal. One deadline-bounded chunk by
    default; `unbounded=True` mines to the end."""
    return _sync(Path(repo), unbounded=unbounded)


def sync_status(repo: str | Path) -> dict:
    """`{"complete": ...}`: whether the checked-out ref's witness (the last commit mined for it)
    is its head, without mining."""
    repo = Path(repo)
    gb = GitBinding(repo)
    key = _ref_key(gb) or gb.head()
    head = gb.head()
    witness = _load_witnesses(repo).get(key)
    return {"complete": witness is not None and witness == head}


def resync(repo: str | Path) -> dict:
    """`ugh check --repair`: drop the current ref's witness, ideal and gate entries and mine them
    again from HEAD, unbounded. The op store is not touched."""
    repo = Path(repo)
    gb = GitBinding(repo)
    key = _ref_key(gb) or gb.head()
    if key is None:
        return {"key": None, "before": 0, "after": 0}

    before = len(_load_ideal_table(repo).get(key, []))
    with locked_section(repo):
        for load, save in (
            (_load_witnesses, _save_witnesses),
            (_load_ideal_table, _save_ideal_table),
            (_load_sync_cache, _save_sync_cache),
        ):
            table = load(repo)
            if key in table:
                del table[key]
                save(repo, table)
    ideal = get(repo, unbounded=True)
    return {"key": key, "before": before, "after": len(ideal.op_ids),
            "complete": sync_status(repo)["complete"]}


def init(repo: str | Path, horizon: str | None = None) -> Ideal:
    """`ugh init`: bind the repo and the store, then mine from the root, or from `horizon` onward
    with deeper history never mined."""
    repo = Path(repo)
    gb = GitBinding(repo)
    gb.init()
    store = Store(repo)
    store.init()

    if horizon is None:
        return get(repo)

    horizon_sha = gb.rev_parse(horizon)
    if horizon_sha is None:
        raise ValueError(f"cannot resolve horizon {horizon!r}")
    return _sync(repo, treat_as_root=horizon_sha)


def commit(repo: str | Path, message: str, trailers: str | None = None) -> str:
    """Commit the working tree, mine the commit like any other, and publish the state ref."""
    repo = Path(repo)
    gb = GitBinding(repo)
    sha = gb.commit_all(message, trailers=trailers)
    get(repo, unbounded=True)
    from ugh.core.sync import state_ref as _state_ref  # lazy: avoid the sync<->lens import cycle
    _state_ref.publish_from_local(gb, repo)
    return sha


def _tracked_paths(repo: Path) -> list[str]:
    """Tracked paths, NUL-delimited so a non-ASCII path is not C-quoted."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, text=True, check=True
    )
    return [path for path in proc.stdout.split("\x00") if path]


def _writes_through_symlink(repo: Path, path: str) -> bool:
    """True if reaching `path` under `repo` traverses a symlink (the leaf or an ancestor)."""
    current = repo
    for part in Path(path).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


_TREE_CLASSES = ("drift", "unmanaged", "unseeded")


def fsck_tree(repo: str | Path) -> dict[str, list[str]]:
    """`ugh check --tree`: the paths where `code(current_ideal)` differs from the HEAD tree.
    `drift` is the record not reproducing HEAD; `unmanaged` is a symlink and `unseeded` a ref never
    mined, both expected. Paths outside ugh's remit (the `ignored` tier) are never compared."""
    repo = Path(repo)
    gb = GitBinding(repo)
    result: dict[str, list[str]] = {k: [] for k in _TREE_CLASSES}
    head = gb.head()
    if head is None:
        return result

    ideal = current_ideal(repo)
    materialized = code(ideal, ops_with_frontier_images(repo, ideal))
    seeded = (_ref_key(gb) or head) in _load_ideal_table(repo)
    cfg = tiers.load_tiers(repo)
    for path in sorted(set(materialized) | set(_tracked_paths(repo))):
        if path.startswith(".sgt/") or materialized.get(path) == gb.blob_bytes(head, path):
            continue
        if _writes_through_symlink(repo, path):
            result["unmanaged"].append(path)
        elif tiers.resolve_tier(path, cfg) == "ignored":
            continue
        elif not seeded:
            result["unseeded"].append(path)
        else:
            result["drift"].append(path)
    return result
