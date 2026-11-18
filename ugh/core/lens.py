"""The lens: get/put integration with git.

`get` mines the commits new to the current ref since its witness, oldest first, persists them into
the store (whose provenance merge on a content-address collision is the identification law), and
returns the ref's ideal: every op mined from the ref's history, reduced to a valid ideal, plus the
working tree's uncommitted edits. Every op comes from mining a commit, ugh's own commits included,
so the ideal only grows and `code(I)` is the ref's tree. A first contact mines the history oldest
first in deadline-bounded chunks; the witness records how far it got.

`commit` commits the working tree as git would and mines the commit like any other.
`init(repo, horizon=...)` mines the horizon commit against the empty tree and never mines deeper
history.
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


_CHUNK_BUDGET_SECONDS = 10.0  # KTD-3: one _sync() chunk's wall-clock ceiling on mine() work.

# An in-progress git merge/cherry-pick/revert leaves conflict-marker bytes (`<<<<<<<`) in the tree
# and a `*_HEAD` pseudo-ref set. Mining that tree would fold the markers into a permanent op, so the
# dirty pass is skipped while one is live (`_sync`) and `save` refuses outright.
_MERGE_PSEUDO_REFS = (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"))


def merge_in_progress(gb: GitBinding) -> str | None:
    """The name of the in-flight git operation (merge/cherry-pick/revert) whose `*_HEAD` pseudo-ref
    is set, or None. The one guard against mining conflict-marker bytes into an op (F26), lifted here
    from `save` so every mine-on-contact path (`revert`/`switch`/read views) shares it."""
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
    """The per-ref no-op gate: `{ref_key: {"fp": <fingerprint>, "ids": [sorted op_ids]}}`. `fp`
    covers everything a re-mine depends on -- HEAD, the dirty working-tree source content, and the
    persisted ideal entry -- so when it's unchanged since the last `_sync`, that sync would return
    the same ideal and can be skipped entirely (the mine's O(files) dirty pass is the bulk of a
    warm `get()`). Local, never travels; an absent file loads as `{}`."""
    return state.load_json(repo, "sync_cache", default={})


def _save_sync_cache(repo: Path, table: dict[str, dict]) -> None:
    state.save_json_if_changed(repo, "sync_cache", table)


def _store_digest(ops) -> str:
    """A digest of the op store's id set, cached beside the fingerprint below.

    The gate needs this separately because the ideal is a function of the *whole store*, not only of
    the ids it happened to cache, and `_sync_fingerprint` cannot see the store: it covers HEAD, the
    dirty working tree, and the persisted ideal entry. Costs one pass over ids the callers have
    already loaded."""
    h = hashlib.sha256()
    for oid in sorted(op.id for op in ops):
        h.update(oid.encode())
        h.update(b"\x00")
    return h.hexdigest()


# Folded into the no-op gate's fingerprint, and bumped when a *writer* bug means entries already on
# disk cannot be trusted -- the same "old entries compare unequal and take one extra sync, no
# migration" trick the `store` key below uses. `MINER_VERSION` is the wrong lever for that: its
# contract (R12) is mining/untangling/identity changes, and bumping it re-mines all history.
# "2": entries written before F130 were keyed to a *second* reading of the working tree taken at write
# time, so a repo wedged by that race (`status` reports drift, `save` says "nothing to save") would
# otherwise stay wedged after upgrading -- the fix stops new poisoning, only eviction heals the old.
_FINGERPRINT_SCHEMA = "2"


def _sync_fingerprint(gb: GitBinding, head: str, ideal_entry, digest: str | None = None,
                     ) -> str | None:
    """The fingerprint the no-op gate compares. None (git couldn't compute the dirty digest) means
    'don't gate -- mine'. `ideal_entry` is the persisted ideal id-list for this ref, so an explicit
    ideal edit (revert/pin, U8) -- which moves neither HEAD nor the tree -- still changes the
    fingerprint and forces a fresh sync. `MINER_VERSION` is folded in so upgrading ugh invalidates
    the memo: a gate that keeps serving a prior version's mining result pins its bugs in place --
    the stale-anchor wedge (2026-08-09) survived its own fix that way until the cache was
    hand-cleared.

    `digest`, if given, is a working-tree digest the caller already sampled, used instead of reading
    the tree again. Writers must pass it (F130): the entry a sync stores is a claim about the tree it
    *mined*, and re-reading the tree seconds later to key that claim attributes the result to
    whatever happens to be on disk by then. Readers pass nothing -- they are asking about the tree
    as it stands now, which is exactly what a fresh sample answers."""
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
# The ideal machinery's expensive derivations -- grounding (`is_valid_ideal`,
# re-run by `Ideal.from_ops` on every construction) and provenance-set reduction
# (`order.reduce_to_ideal`) -- are pure functions of the *content-addressed id set* they're
# given. A CLI command is a
# fresh process, so the kernel's in-process memos never carry across commands, and every command
# re-paid the same O(ops) derivations for the same unchanged ideals. These stamps persist the
# digest of each id set that passed validation (and each reduction's dropped-set) so the *next*
# process skips the derivation. What is NOT stamped, and re-runs live on every call, is the
# presence check (`ids <= present`): a `git switch` can remove op files while the digest is
# unchanged, and presence is the one input that isn't fixed by the ids themselves.
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
    """Trim to the caps (drop oldest) and persist. Only called when an entry was just added, so a
    steady-state read never writes. Concurrent writers last-win; losing an entry only means one
    future re-derivation -- this is a cache, `.sgt/ops` stays the only authority."""
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
    ideal = Ideal.from_ops(ids, ops)  # raises on an invalid set, exactly as before
    st["valid"].append(digest)
    st["valid_set"].add(digest)
    _derive_record(repo, st)
    return ideal


def _reduced_ideal_ids(repo: Path, raw_ids, ops: list) -> frozenset[str]:
    """`order.reduce_to_ideal` with the persisted dropped-set stamp:
    the reduction of a given present-op id set is fixed by content addressing, so only the
    (usually tiny) dropped set is stored and the result is `present-filtered ids - dropped`."""
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
    # Persist whichever side is smaller -- a mostly-linear ref drops a handful, while the
    # maximal reduction over a store with unfinished backfills can drop most of it.
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


def ops_with_frontier_images(
    repo: str | Path, ideal: Ideal, for_paths: "set[str] | None" = None
) -> list:
    """The footprint-only index ops, with the *frontier producers'* full ops (images included)
    substituted in -- exactly the set `fold.code` reads images from when materializing `ideal`.
    `Store.all_ops()` decodes every op's images (85%+ of the store's bytes) yet a materializing
    read only ever opens the images of the ops at the ideal's frontier, so a view that needs
    `code(ideal, ops)` plus footprint-level queries can use this and skip the full decode. Do
    NOT hand the result to anything folding a *different* ideal -- its frontier ops would carry
    `images={}` (empty, not absent) and fold to zero-length content.

    `for_paths`, if given, narrows the fetch to the producers of symbols on those paths -- for a
    caller that will fold only those paths (`fold.code(..., only_paths=for_paths)`, the backstop
    reads). Folding any other path from such a list would hit the imageless ops, so the two
    restrictions must always travel together."""
    from concurrent.futures import ThreadPoolExecutor

    repo = Path(repo)
    ops = opindex.index_ops(repo)
    tip = ideal.frontier(ops)
    if for_paths is not None:
        tip = {sym: oid for sym, oid in tip.items() if sym.split("::", 1)[0] in for_paths}
    need = sorted(set(tip.values()))
    store = Store(repo)

    def _safe_get(oid: str):
        # Mirror `Store.all_ops`' R1 skip: a truncated/corrupt frontier op file degrades to a
        # read-side skip rather than propagating out of `pool.map` and erroring a read view
        # (`status_view`, `fsck_tree`, `_reproducible_content`) -- ops must never error.
        try:
            return store.get(oid)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        full = list(pool.map(_safe_get, need))
    by_id = {op.id: op for op in full if op is not None}
    # A frontier producer whose full op couldn't be read (corrupt, or vanished after the index
    # snapshot) is dropped entirely -- exactly as `all_ops` would exclude it -- not kept
    # footprint-only: its `images={}` would fold to silent zero-length content for the symbols it
    # produces, which is a worse failure than the op's absence.
    unreadable = set(need) - set(by_id)
    return [by_id.get(op.id, op) for op in ops if op.id not in unreadable]


def current_ideal(repo: str | Path) -> Ideal:
    """The current ref's committed ideal as last mined -- a pure read: the cached table entry, or
    the provenance scan when the ref has none yet."""
    repo = Path(repo)
    gb = GitBinding(repo)
    store = Store(repo)
    key = _ref_key(gb)
    table = _load_ideal_table(repo)
    ids = frozenset(table[key]) if key is not None and key in table else _committed_ids_by_provenance(gb, store)
    return _validated_from_ops(repo, ids, opindex.index_ops(repo))


def ideal_for_ref(repo: str | Path, ref: str = "HEAD") -> Ideal:
    """The ideal `ref`'s history implies, a pure read: this miner's ops whose provenance is in
    `ref`'s ancestry, grounded. A ref whose history was never mined yields less; `get()` it first."""
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
    """`unbounded=True` drops the per-chunk wall-clock ceiling, so this call mines whatever is
    left rather than whatever fits in `_CHUNK_BUDGET_SECONDS`. For callers whose contract is
    completeness (`resync`) rather than interactivity -- see the note on the deadline sites."""
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

    # The tree digest is sampled before mining and keys this sync's cache entry (F130), so an edit
    # landing mid-sync misses the gate next time. An in-progress git merge is never mined (F26).
    dirty_digest_at_mine = gb.dirty_source_digest()
    include_dirty = gb.has_dirty_source() and merge_in_progress(gb) is None

    # History is mined oldest first, so every commit's parent is mined before it: versions are read
    # from history. A deadline cuts one call to a chunk; the witness records how far it got.
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

    # Staleness is judged before our own `store.add` writes, which always make the snapshot look
    # stale; stale here means someone else wrote ops the snapshot missed.
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

    # The ideal is every op mined from this ref's history, reduced to a valid ideal; the table is
    # its cache.
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

    # The in-memory ideal carries the dirty overlay on top of the durable committed set.
    result = Ideal.from_ops(order.reduce_to_ideal(committed_ids | pending_ids, all_ops), all_ops)

    if treat_as_root is None and new_witness == head:
        fp = _sync_fingerprint(gb, head, sorted(committed_ids), digest=dirty_digest_at_mine)
        if fp is not None:
            table = _load_sync_cache(repo)
            table[key] = {"fp": fp, "ids": sorted(result.op_ids), "store": _store_digest(all_ops)}
            _save_sync_cache(repo, table)
    return result


def get(repo: str | Path, *, unbounded: bool = False) -> Ideal:
    """Mine what's new to the current ref, persist it, and return the ref's current ideal.

    Deadline-chunked by default so an interactive read cannot hang; `unbounded=True` mines to
    the end instead, for a caller that needs the record complete rather than prompt."""
    return _sync(Path(repo), unbounded=unbounded)


def sync_status(repo: str | Path, ref: str | None = None) -> dict:
    """A pure read (no mining): `complete` is True iff `ref`'s witness (the last commit mined for
    it) is its head. `ref=None` reports on the currently checked-out ref."""
    repo = Path(repo)
    gb = GitBinding(repo)
    if ref is None:
        key = _ref_key(gb) or gb.head()
        head = gb.head()
    else:
        head = gb.rev_parse(ref)
        key = ref if ref.startswith("refs/") else f"refs/heads/{ref}"
    witness = _load_witnesses(repo).get(key)
    return {"complete": witness is not None and witness == head}


def resync(repo: str | Path) -> dict:
    """`ugh check --repair`: drop the current ref's witness, ideal and sync cache and re-derive them
    from what HEAD reaches now, unbounded (the caller treats the return as "the record matches
    HEAD"). The op store is never touched."""
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
    """`ugh init`: bind (or reuse) the repo and the kernel store, then mine -- from genesis, or
    from `horizon` onward if given (R10)."""
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
