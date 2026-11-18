"""The lens: get/put integration with git.

`get` mines the commits new to the current ref since its witness, oldest first, persists them into
the store (whose provenance merge on a content-address collision is the identification law), and
returns the ref's ideal: every op mined from the ref's history, reduced to a valid ideal, plus the
working tree's uncommitted edits. Every op comes from mining a commit, ugh's own commits included,
so the ideal only grows and `code(I)` is the ref's tree. A first contact mines the history oldest
first in deadline-bounded chunks; the witness records how far it got.

`put` writes `code(I)` to the working tree and commits it with `Sgt-Op:` trailers; `commit` mines
that commit like any other. `init(repo, horizon=...)` mines the horizon commit against the empty
tree and never mines deeper history.
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
from ugh.store.gitbind import GitBinding, format_op_trailers, parse_op_ids


class DirtyWorkingTreeError(Exception):
    """`put()` would overwrite uncommitted working-tree changes with different bytes (R9). Raised
    instead of silently clobbering; the caller absorbs the edit first (`get()` folds a dirty tree
    into the ideal) so the materialization reproduces it rather than reverting it."""


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
# The ideal machinery's expensive derivations -- grounding + fork-freedom (`is_valid_ideal`,
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
    """`Ideal.from_ops` with the persisted validity stamp: a stamped digest skips grounding +
    fork-freedom (fixed by the content-addressed ids), while presence is checked live either way. An unstamped set takes the full check and earns its stamp."""
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
    """This ref's ideal derived from history: every op mined from its ancestry, reduced to a valid
    ideal (forked tips and what stands on them dropped; forks live in `.sgt/forks.json`)."""
    all_ops = opindex.index_ops(store.repo)
    return set(_reduced_ideal_ids(store.repo, _provenance_ids_at(gb, "HEAD", all_ops), all_ops))


def _record_parked_forks(repo: Path, parked: list[tuple[str, str, str]]) -> None:
    """1.4: union the forks this rebuild parked into the one shared `.sgt/forks.json` store so
    `ugh forks`/`resolve` surface them. Additive -- it never clears a record `land`'s refusal (F23)
    or a teammate's `sync` flush wrote; a fork leaves the store only when its resolution drops it
    from the rebuild and a replacing writer rewrites, or `resolve` removes it (the CRDT-tombstone
    lifecycle is Phase 4). Routes through the single `save_fork_records` writer (never a bespoke
    `save_json`) and writes only when the union gains something, so a re-`get()` over the same fork
    touches no mtime (R5). Its own lock guards the read-modify-write against a concurrent `sync`
    flush; `locked_section` is non-reentrant (U23), so this must run *outside* the checkpoint
    section. `materialize` imports `lens` at module load, so the reverse import is lazy to avoid a
    cycle."""
    if not parked:
        return
    from ugh.core.sync import materialize

    with locked_section(repo):
        existing = {
            (r["symbol"], r["tips"][0], r["tips"][1])
            for r in state.load_json(repo, "forks", default=[])
        }
        merged = existing | set(parked)
        if merged != existing:
            materialize.save_fork_records(repo, tuple(sorted(merged)))


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
    """The current ref's committed ideal as last persisted -- a *pure read* (no mining, no
    writes), reflecting any prior explicit edit (revert/pin, U8) that a provenance scan alone
    would miss. Falls back to the provenance-scan seed when the ref has no persisted entry yet.
    This is the plan-time view an ideal-edit verb previews against (`--emit` must be
    side-effect-free); `get()` is the mine-on-contact version `apply` runs to absorb reality."""
    repo = Path(repo)
    gb = GitBinding(repo)
    store = Store(repo)
    key = _ref_key(gb)
    table = _load_ideal_table(repo)
    ids = frozenset(table[key]) if key is not None and key in table else _committed_ids_by_provenance(gb, store)
    return _validated_from_ops(repo, ids, opindex.index_ops(repo))


def _recorded_ideal_at(gb: GitBinding, ref: str) -> frozenset[str] | None:
    """The op-set a ref's tip commit *records* in its own `Sgt-Op:` trailers, or None when it has
    none (pre-ugh history, a plain `git commit`, a rewrite that stripped them).

    The tip alone is enough, and is better than walking: ugh writes the whole ideal into every
    commit `put` makes (`format_op_trailers(sorted(ideal.op_ids))`), so the trailers
    are cumulative and each commit's set is already complete. Measured on a 13-commit React repo,
    every commit's trailers are a valid ideal on their own, growing 31 -> 151 ops and folding
    14 -> 30 files. That also settles the merge case without needing a walk policy: a two-parent
    `ugh sync` merge records the full *merged* ideal in its own trailers (verified: 12 trailers,
    12 ops in the resulting ideal), where any single-parent walk would have under-counted a whole
    merged-in branch.

    An empty parse is treated as "no trailers" rather than "the empty ideal", so a commit carrying
    only bookkeeping trailers falls back rather than claiming a repo has no ops.

    An unresolvable ref returns None rather than raising: `commit_shas` answers `[]` for one
    ("Empty before the first commit, or if `ref` doesn't resolve"), so the provenance rung already
    degrades gracefully, and `commit_message` raising here would turn `ugh advanced fold --at
    no-such-branch` from a clean refusal into a `GitError` traceback."""
    try:
        ids = parse_op_ids(gb.commit_message(ref))
    except Exception:  # noqa: BLE001 -- an unresolvable/unborn ref is the fallback's business
        return None
    return frozenset(ids) if ids else None


def ideal_for_ref(repo: str | Path, ref: str = "HEAD", store: Store | None = None) -> Ideal:
    """The ideal a given ref's committed history implies -- a *pure read*: no mining, no
    checkout, no side effects.

    Two sources, in order, and never a union of them (a union would produce a set that is neither):

    1. The ref tip's own `Sgt-Op:` trailers (`_recorded_ideal_at`) -- the committed, tree-witnessed
       record of which ops that commit's tree embodies.
    2. Failing that, provenance projection: the ops whose `provenance` intersects `ref`'s commit
       ancestry, exactly as `_committed_ids_by_provenance` does for the current ref.

    Rung 1 exists because rung 2 alone is not merely approximate, it collapses. `op.provenance` is
    stamped only by *mining* pre-existing git history; ops minted by `ugh save` and by the planner
    carry none, so no ref can ever select them. Measured on a React app authored the way ugh asks
    people to author -- 13 commits, all but the scaffold made by `ugh save` -- provenance projection
    returned **10 of 151 ops and 7 of 30 files, with 0 of its 23 source files**, while its trailers
    named all 151. The same repo shape mined from real git commits instead loses far less (bikecount:
    111 of 189), which is why this went unnoticed: the convenient instrumented repo was the one
    shape that hides it.

    This still never consults the persisted `.sgt/local/ideal.json` table (see `_sync`). Trailers
    live in the commit, so reading a ref's own trailers is what "the ideal this ref's committed
    history implies" already means -- the same source `sync`'s log -> trailers -> mine recovery
    ladder trusts (`sync.ingest._attributed_ids`). A rewrite that strips trailers falls to rung 2,
    i.e. exactly today's answer and never a worse one; `ugh check --repair` remains the remedy
    there.

    A ref whose history was never mined *and* carries no trailers still yields an under-approximated
    ideal, so contact it with `get()` first for completeness. The read views (U7's
    `state_view`/`ideal_diff_view`) use this to inspect and compare refs without disturbing the
    working tree.

    `store` is accepted (unused) for call-site compatibility -- this is a pure ideal-derivation,
    so it reads the footprint-only `opindex` sidecar (never `op.images`) rather than a caller-
    supplied `Store`."""
    repo = Path(repo)
    gb = GitBinding(repo)
    all_ops = opindex.index_ops(repo)

    included = _recorded_ideal_at(gb, ref)
    if included is None:
        included = _provenance_ids_at(gb, ref, all_ops)
    # `_reduced_ideal_ids` drops ids with no op in the store up front, so a trailer naming an op
    # this clone has not fetched degrades to a smaller valid ideal rather than raising.
    return _validated_from_ops(repo, _reduced_ideal_ids(repo, included, all_ops), all_ops)


def _provenance_ids_at(gb: GitBinding, ref: str, all_ops: list) -> set[str]:
    """This miner version's ops whose `provenance` intersects `ref`'s commit ancestry."""
    ref_commits = set(gb.commit_shas(ref))
    return {op.id for op in all_ops
            if op.miner_version == MINER_VERSION and not ref_commits.isdisjoint(op.provenance)}


def provenance_ideal_for_ref(repo: str | Path, ref: str = "HEAD", store: Store | None = None) -> Ideal:
    """Everything `ref`'s ancestry ever recorded, including ops a revert has since taken out.

    Deliberately not `ideal_for_ref`. That one answers "the ideal this ref's tree embodies", which
    is what `--at <ref>` and `fold` want, and after a revert it correctly *excludes* the reverted
    op. `restore` needs the opposite: the whole point of the verb is to address something the
    current ideal no longer holds, so its source has to be the superset that still holds it.
    Reverting an op does not remove the commit that introduced it from the ancestry, so provenance
    still selects it where the tip's `Sgt-Op:` trailers -- which record the post-revert ideal --
    no longer do.

    Split out because `plan_restore` used to get this by calling `ideal_for_ref`, back when that
    was provenance-only. When `ideal_for_ref` grew its trailers rung, restore silently kept
    resolving `file::sym` to the still-live older version instead of the reverted one, so its
    downset was already in the ideal and it applied nothing while reporting `ok` -- a verb that
    moves files reporting success over an untouched tree.

    `store` is accepted (unused) for call-site compatibility, as on `ideal_for_ref`."""
    repo = Path(repo)
    all_ops = opindex.index_ops(repo)
    included = _provenance_ids_at(GitBinding(repo), ref, all_ops)
    return _validated_from_ops(repo, _reduced_ideal_ids(repo, included, all_ops), all_ops)


def _sync(repo: Path, treat_as_root: str | None = None, *, unbounded: bool = False) -> Ideal:
    """`unbounded=True` drops the per-chunk wall-clock ceiling, so this call mines whatever is
    left rather than whatever fits in `_CHUNK_BUDGET_SECONDS`. For callers whose contract is
    completeness (`resync`) rather than interactivity -- see the note on the deadline sites."""
    gb = GitBinding(repo)
    store = Store(repo)
    store.init()

    head = gb.head()
    if head is None:
        # Bootstrap (R9 from day zero): a repo with no commits yet can still carry mineable source
        # in an untracked/dirty working tree -- that is exactly the state a fresh `ugh init` leaves,
        # and the first `ugh save` must be able to capture it into the first witness commit. The
        # main sync body below keys every persisted table (witness/ideal/backfill/cache) on a
        # committed ref that does not exist yet, so here we run only the dirty mining pass, persist
        # the resulting pending ops into the store + opindex, and return them as the current ideal.
        # `put()` then materializes the first real commit; every later `get()` takes the ordinary
        # first-contact path. A clean tree still yields the empty ideal, exactly as before.
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

    _record_parked_forks(repo, order.parked_forks(seed, all_ops))

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


def put(repo: str | Path, ideal: Ideal, message: str = "ugh: materialize ideal",
        *, bookkeeping: bool = False) -> str:
    """`code(I)` -> working tree -> a witness commit carrying one `Sgt-Op:` trailer per op the
    new tree embodies. Mine-before-materialize (R9): `get()` runs first so a dirty tree or a
    foreign commit is absorbed into the store, then the fold is refused (rather than silently
    clobbering) if it would overwrite an uncommitted change with different bytes.

    The commit is then mined like any other (`commit`). `bookkeeping=True` marks it as ugh's own
    mechanics so human-facing lists can fold it."""
    repo = Path(repo)
    gb = GitBinding(repo)
    get(repo)  # absorb any dirty tree / foreign commit first (R9)
    store = Store(repo)
    all_ops = store.all_ops()
    materialized = code(ideal, all_ops)
    conflicts = _dirty_conflicts(repo, gb, materialized)
    if conflicts:
        raise DirtyWorkingTreeError(
            f"put() would overwrite uncommitted changes: {sorted(conflicts)} "
            f"(if you just rewrote git history -- reset/amend/branch -f -- run `ugh check --repair`)"
        )
    # Delta-scoped guard (Phase 0, 0.1): the fold rewrites *every* covered path, but this edit only
    # touches the symbols in `before_ideal Δ after_ideal`. A path outside that delta whose on-disk
    # bytes differ from what the ideal materializes is committed drift the fold would silently roll
    # back -- a local merge/cherry-pick the miner mis-attributed (F7/F9), or a foreign edit. Refuse
    # and name it rather than overwrite it; `_dirty_conflicts` cannot catch this because the drift
    # is committed (on-disk == HEAD), not an uncommitted change.
    before_ideal = current_ideal(repo)
    delta_files = _delta_paths(before_ideal.op_ids ^ ideal.op_ids, all_ops)
    drift = _outside_delta_drift(repo, materialized, delta_files)
    if drift:
        raise DirtyWorkingTreeError(
            "put() would roll back files outside this edit's scope, whose committed content "
            f"differs from ugh's recorded ideal: {sorted(drift)}"
        )
    _write_working_tree(repo, materialized, all_ops, prior_ideal=before_ideal)
    trailers = format_op_trailers(sorted(ideal.op_ids))
    if bookkeeping:
        from ugh.store.gitbind import format_bookkeeping_trailer
        trailers = f"{trailers}\n{format_bookkeeping_trailer()}" if trailers else format_bookkeeping_trailer()
    return commit(repo, message, trailers)


def commit(repo: str | Path, message: str, trailers: str | None = None) -> str:
    """Commit the working tree, mine the commit like any other, and publish the state ref."""
    repo = Path(repo)
    gb = GitBinding(repo)
    sha = gb.commit_all(message, trailers=trailers)
    get(repo, unbounded=True)
    from ugh.core.sync import state_ref as _state_ref  # lazy: avoid the sync<->lens import cycle
    _state_ref.publish_from_local(gb, repo)
    return sha


def _dirty_conflicts(repo: Path, gb: GitBinding, materialized: dict[str, bytes]) -> set[str]:
    """Paths where `put()` would clobber an uncommitted change. A path is *dirty* when its
    on-disk bytes differ from HEAD's; it *conflicts* only when the ideal would additionally
    materialize different bytes there (or delete it). A dirty path the ideal already reproduces
    exactly -- the normal case once `get()` has folded the edit into the ideal -- is not a
    conflict, so the intended get()->put() flow never trips this guard. Only paths `put()` would
    actually write or delete are considered (`materialized` keys plus git-tracked paths); an
    untracked file the ideal doesn't cover is left untouched by `_write_working_tree`, so it can't
    conflict. `.sgt/` is skipped -- it's ugh's own state, not codebase content `put()` owns."""
    head = gb.head()
    tracked = set(_tracked_paths(repo))
    ignored_tier = _outside_ughs_remit(repo)

    conflicts: set[str] = set()
    for path in set(materialized) | tracked:
        if path.startswith(".sgt/") or _writes_through_symlink(repo, path):
            continue  # symlinks are unmanaged (R3) -- never read/written through here either
        if path not in materialized and ignored_tier(path):
            continue  # ugh never mines it, so the fold never writes or deletes it -- not a conflict
        full = repo / path
        on_disk = full.read_bytes() if full.is_file() else None
        committed = gb.blob_bytes(head, path) if head is not None else None
        if on_disk != committed and materialized.get(path) != on_disk:
            conflicts.add(path)
    return conflicts


def _delta_paths(delta_op_ids: frozenset[str], all_ops: list) -> set[str]:
    """The working-tree paths the ops in `delta_op_ids` touch -- the scope a materializing edit is
    allowed to rewrite (0.1). `before_ideal Δ after_ideal` is exactly the ops whose presence
    changed, so their footprint paths are the only ones the fold should alter; a path outside this
    set is produced identically by both ideals, so any on-disk difference there is drift, not this
    edit's doing."""
    paths: set[str] = set()
    for op in all_ops:
        if op.id in delta_op_ids:
            paths.update(sym.split("::", 1)[0] for sym in op.footprint)
    return paths


def _outside_delta_drift(repo: Path, materialized: dict[str, bytes], delta_files: set[str]) -> set[str]:
    """Paths `put()` would rewrite that lie OUTSIDE this edit's op-delta yet whose on-disk bytes
    differ from what the ideal materializes (0.1). For such a path both the outgoing and incoming
    ideal produce the same bytes, so a difference is committed drift the fold would silently roll
    back (F7/F9) -- refused, never overwritten. Only paths the fold actually writes are considered
    (`materialized` keys); `.sgt/` and symlinks are skipped exactly as `_dirty_conflicts` skips
    them. Deletes of unmanaged drift stay covered by `_write_working_tree`'s reproducibility
    backstop, so they are not re-checked here."""
    drift: set[str] = set()
    for path, data in materialized.items():
        if path in delta_files or path.startswith(".sgt/") or _writes_through_symlink(repo, path):
            continue
        full = repo / path
        on_disk = full.read_bytes() if full.is_file() else None
        if data != on_disk:
            drift.add(path)
    return drift


def _outside_ughs_remit(repo: Path) -> "callable":
    """A predicate for paths ugh deliberately never mines -- the `ignored` tier: dot-paths
    (`.gitignore`, `.github/workflows/ci.yml`), gitignored paths, `.sgtignore` matches. `code()`
    cannot produce such a path, which is not the same fact as the ideal having dropped it, and the
    difference is the whole of this predicate: the fold must neither delete one nor refuse because
    one is dirty. Reading them as ideal-excluded made a single uncommitted `.gitignore` line block
    `save`, `undo`, `revert --yes` and `restore` at once, and the remedy the error named (`ugh
    save`) reported `nothing to save`, because an ignored path mints no op -- so the only way out
    ran through git, which is the tool ugh is meant to stand in front of. An `entity`/`opaque`
    override in `.sgt/tiers.json` force-includes a path, and `resolve_tier` honours it here too, so
    a deliberately-included dotfile keeps the guard.

    The config is read once per fold; the returned closure is called per path."""
    cfg = tiers.load_tiers(repo)
    return lambda path: tiers.resolve_tier(path, cfg) == "ignored"


def _tracked_paths(repo: Path) -> list[str]:
    """Tracked paths, NUL-delimited (F72). Plain `ls-files` C-quotes any path containing non-ASCII
    bytes, and the quoted literal names nothing on disk -- so the `is_file()` guard in `_status_paths`
    silently dropped it and the path fell out of `backstop_kept`, `unmanaged`, and `drift` alike. A
    file ugh cannot reproduce was then reported as nothing at all. `-z` emits raw bytes instead.
    Symlink and gitlink entries stay in the list: `unmanaged` is built from the symlinks, and a
    gitlink's path is a directory, which the callers' own `is_file()` checks already exclude."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, text=True, check=True
    )
    return [path for path in proc.stdout.split("\x00") if path]


def _writes_through_symlink(repo: Path, path: str) -> bool:
    """True if reaching ``path`` under ``repo`` would traverse a symlink -- the leaf itself is a
    symlink, or any ancestor directory between the repo root and the leaf is. The on-disk twin of
    mine's mode-120000 skip (R3): symlinks are unmanaged, so a verb must never write or delete
    *through* one (following it could clobber a target outside the repo). `lstat` at each step,
    never following."""
    current = repo
    for part in Path(path).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _reproducible_content(
    repo: Path, all_ops: list | None = None, only_paths: "set[str] | None" = None
) -> dict[str, bytes]:
    """Every path `code()` can produce from the store's *maximal valid ideal* -- all stored ops
    reduced to a grounded, fork-free set. A path present here is recoverable, so deleting its live
    bytes is safe; a path whose current bytes are absent (e.g. a dropped add/delete/re-add fork
    tip) is not (R4). Without a caller-supplied `all_ops`, reads frontier-selectively: reduce the
    maximal ideal over the footprint-only index, then load images for just its frontier producers
    -- the only images `code` opens -- instead of `Store.all_ops()`'s every-op decode.

    Every caller consults the result for a small known path set (the paths a delete would touch),
    so `only_paths` narrows both the image fetch and the fold to exactly those paths -- the
    difference between reading a handful of op files and the full maximal frontier's thousands."""
    if all_ops is None:
        index = opindex.index_ops(repo)
        maximal = _validated_from_ops(
            repo, _reduced_ideal_ids(repo, {op.id for op in index}, index), index
        )
        ops = ops_with_frontier_images(repo, maximal, for_paths=only_paths)
        return code(maximal, ops, only_paths=only_paths)
    maximal = order.reduce_to_ideal({op.id for op in all_ops}, all_ops)
    return code(Ideal.from_ops(maximal, all_ops), all_ops, only_paths=only_paths)


def materialization_skips(
    repo: str | Path, materialized: dict[str, bytes], all_ops: list | None = None,
    *, prior_ideal: Ideal | None = None,
) -> dict[str, list[str]]:
    """What `_write_working_tree` would refuse to touch, computed *without* writing -- for `status`
    to surface (R3/R4). `unmanaged`: tracked symlink paths. `backstop_kept`: tracked paths the
    current ideal dropped whose live bytes no valid ideal over the store can regenerate, *and*
    which the store has ops for -- a chain that may genuinely be repairable. `never_recorded`:
    the same shape, but the store holds no op for the path at all.

    The split exists because the two need opposite things said about them. A path ugh never mined
    -- `.gitignore` in every repo built by these tools -- cannot be materialized by any repair, so
    reporting it as damage and pointing at `ugh check --tree` sends a user to run a command
    that answers "0 drifted path(s)" and leaves the same warning on the next `status`.

    `prior_ideal` mirrors `_write_working_tree`'s parameter of the same name, and every caller
    predicting a *specific* edit must pass it (`status`, which asks about no particular edit,
    does not). These two functions are
    a matched pair -- this one exists only to say what that one would do -- so a keep-condition
    added there and not here silently makes the prediction disagree with the apply. Not
    hypothetical: when `prior_ideal` landed in the writer alone, `revert --emit --out` went on
    keeping three bikecount pages the apply had begun deleting, which is the same class of defect
    as the omission it was added to fix, pointing the other way."""
    repo = Path(repo)
    tracked = _tracked_paths(repo)
    unmanaged = [p for p in tracked if _writes_through_symlink(repo, p)]
    to_delete = [
        p for p in tracked
        if p not in materialized and not p.startswith(".sgt/")
        and (repo / p).is_file() and not _writes_through_symlink(repo, p)
    ]
    reproducible = _reproducible_content(repo, all_ops, only_paths=set(to_delete)) if to_delete else {}
    prior: dict[str, bytes] = (
        code(prior_ideal, all_ops, only_paths=set(to_delete))
        if prior_ideal is not None and all_ops is not None and to_delete else {}
    )
    kept = [
        p for p in to_delete
        if (repo / p).read_bytes() != reproducible.get(p)
        and (repo / p).read_bytes() != prior.get(p)
    ]
    # Which of those the store has ever mined an entity for. A footprint symbol is
    # `path::entity`, so the path prefix is the test.
    ops = all_ops if all_ops is not None else Store(repo).all_ops()
    have_ops = {sym.split("::", 1)[0] for op in ops for sym in op.footprint}
    return {
        "unmanaged": sorted(set(unmanaged)),
        "backstop_kept": sorted(p for p in kept if p in have_ops),
        "never_recorded": sorted(p for p in kept if p not in have_ops),
    }


_TREE_CLASSES = ("drift", "unmanaged", "backstop_kept", "unseeded")


def fsck_tree(repo: str | Path) -> dict[str, list[str]]:
    """`ugh check --tree` (R2): compare `code(current_ideal)` against the HEAD tree and classify
    every divergent path. Only `drift` is a real finding -- bytes at HEAD that ugh never absorbed
    (`get` to absorb, or `put` to enforce the ideal, with opposite data-loss profiles). The rest
    are planned divergence: `unmanaged` (a symlink), `backstop_kept` (a path the ideal dropped
    whose HEAD bytes no valid ideal can regenerate), or `unseeded` (a ref this lens never tracked -- a fresh clone or detached HEAD, not drift)."""
    repo = Path(repo)
    gb = GitBinding(repo)
    result: dict[str, list[str]] = {k: [] for k in _TREE_CLASSES}
    head = gb.head()
    if head is None:
        return result

    ideal = current_ideal(repo)
    all_ops = ops_with_frontier_images(repo, ideal)  # fsck --tree folds only the current ideal
    materialized = code(ideal, all_ops)
    key = _ref_key(gb) or head
    seeded = key in _load_ideal_table(repo)

    candidates = set(materialized) | set(_tracked_paths(repo))
    reproducible: dict[str, bytes] | None = None
    for path in sorted(candidates):
        if path.startswith(".sgt/"):
            continue
        mat = materialized.get(path)
        head_bytes = gb.blob_bytes(head, path)
        if mat == head_bytes:
            continue  # the ideal reproduces HEAD's bytes exactly -- no divergence
        if _writes_through_symlink(repo, path):
            result["unmanaged"].append(path)
        elif not seeded:
            result["unseeded"].append(path)
        elif mat is None and head_bytes is not None:
            if reproducible is None:
                # `None`, not `all_ops`: the reproducibility read folds the *maximal* ideal,
                # whose frontier reaches ops our frontier-selective list carries imageless.
                # Restricted to the paths this loop can still ask about -- the candidates the
                # current ideal doesn't materialize -- not the maximal frontier's full sweep.
                reproducible = _reproducible_content(
                    repo, None, only_paths={p for p in candidates if p not in materialized}
                )
            (result["backstop_kept"] if head_bytes != reproducible.get(path)
             else result["drift"]).append(path)
        else:
            result["drift"].append(path)
    return result


def _write_working_tree(
    repo: Path, materialized: dict[str, bytes], all_ops: list | None = None,
    *, prior_ideal: Ideal | None = None,
) -> dict[str, list[str]]:
    """Write every materialized path; delete any git-tracked path the ideal no longer covers --
    the fold is total, so an absent path means the ideal genuinely doesn't include it. Two safety
    guards keep the fold non-destructive (R3/R4): never write or delete *through* a symlink (leaf
    or ancestor), and never delete a tracked path whose live bytes no valid ideal can regenerate
    (the backstop -- an add/delete/re-add fork drops a path from `code(I)` though its content is
    genuinely live, and a silent delete there is unrecoverable). Returns the skipped paths
    (`unmanaged`, `backstop_kept`) so the caller can surface them instead of losing them silently.

    `prior_ideal` is the ideal this write is editing *away from* (`put` passes it; nobody else has
    one). It counts as a second regenerating ideal for the backstop, and it has to: an edit that
    removes a path mints the ops that express the removal, which are stored before `put`
    runs, and `_reproducible_content`'s maximal ideal then takes every chain to its tip -- this
    edit's own prunes included. The one ideal the backstop consults is therefore the one ideal that
    can no longer produce the bytes being deleted, so a revert that empties a file whose entity was
    forward-pruned rather than excluded refused its own delete, committed nothing, and reported
    success."""
    tracked = _tracked_paths(repo)

    unmanaged: list[str] = []
    for path, data in materialized.items():
        if _writes_through_symlink(repo, path):
            unmanaged.append(path)
            continue
        full = repo / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)

    ignored_tier = _outside_ughs_remit(repo)
    to_delete = [
        p for p in tracked
        if p not in materialized and not p.startswith(".sgt/") and (repo / p).is_file()
        and not ignored_tier(p)  # never mined, so its absence from the ideal means nothing
    ]
    backstop_kept: list[str] = []
    reproducible: dict[str, bytes] | None = None
    prior: dict[str, bytes] = {}
    for path in to_delete:
        if _writes_through_symlink(repo, path):
            unmanaged.append(path)
            continue
        if reproducible is None:
            reproducible = _reproducible_content(repo, all_ops, only_paths=set(to_delete))
            if prior_ideal is not None and all_ops is not None:
                prior = code(prior_ideal, all_ops, only_paths=set(to_delete))
        full = repo / path
        live = full.read_bytes()
        if live != reproducible.get(path) and live != prior.get(path):
            backstop_kept.append(path)  # live bytes no valid ideal can regenerate -- keep (R4)
            continue
        full.unlink()

    return {"unmanaged": sorted(set(unmanaged)), "backstop_kept": sorted(backstop_kept)}
