"""The lens: get/put integration with git (ADR S6; plan R8, R9, R10, R20).

`get` mines any commits new to the current ref since that ref's last witness, persists them
into the store (whose provenance-merge on a content-address collision *is* the identification
law, R8), then reconstructs the ref's current ideal as every stored op whose provenance
intersects the ref's own commit ancestry. A squash merge or rebase lands its result as a new
witness commit on ops that already exist (same content, same id), so it's absorbed into the
ref's ideal rather than forking (AE1) -- no special-casing, this falls out of content-addressing
plus ref-ancestry membership. A `git checkout` to a ref this lens has never tracked mines that
ref's own history cold; that's slower, never wrong, since re-mining already-known content just
re-derives the same op ids and merges witnesses.

`put` runs `code(I)` and writes the result to the working tree (deleting any git-tracked path
the ideal no longer covers), then commits with `Sgt-Op:` trailers naming every op the tree now
embodies. Mine-before-materialize (R9) is why every mutating verb should call `get` before
computing its edit: a dirty working tree or a foreign commit made outside sgt is absorbed first,
so the verb's own change lands on top of *current* reality, not stale state.

`init(repo, horizon=...)` is the genesis-horizon mechanism (R10): pre-horizon history is never
mined at all -- everything at the horizon commit becomes one add-op per symbol (via `mine`'s
`treat_as_root`), and mining continues normally from there to HEAD. Lazy background mining of
pre-horizon history is deliberately out of scope here (plan Scope Boundaries).

A ref with no horizon that `get()` meets for the first time does not mine its full history in one
shot: `_sync` bootstraps its witness to HEAD immediately, then walks the rest of that history
backward one deadline-bounded chunk per call, checkpointing its genesis-backfill frontier after
every chunk (see `_sync`'s own comments). So a client with a bounded per-call timeout makes
durable forward progress on a never-before-synced ref instead of restarting from scratch on every
retry.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from sgt import state
from sgt.core import opindex, order
from sgt.core.fold import code
from sgt.core.ideal import Ideal
from sgt.core.mine import mine
from sgt.core.store import Store, locked_section
from sgt.store.gitbind import GitBinding, format_op_trailers


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
        if gb.rev_parse(pseudo) is not None:
            return verb
    return None


def _load_witnesses(repo: Path) -> dict[str, str]:
    return state.load_json(repo, "witness", default={})


def _save_witnesses(repo: Path, table: dict[str, str]) -> None:
    state.save_json(repo, "witness", table)


def _load_ideal_table(repo: Path) -> dict[str, list[str]]:
    """The persisted per-ref ideal: `{ref_key: [sorted op_ids]}`. This is the durable committed
    ideal (never the dirty overlay) that lets an explicit ideal edit (revert/pin, U8) survive a
    re-`get()` -- provenance alone can't represent "excluded though still in git history"."""
    return state.load_json(repo, "ideal_table", default={})


def _save_ideal_table(repo: Path, table: dict[str, list[str]]) -> None:
    state.save_json(repo, "ideal_table", table)


@dataclass(frozen=True)
class ExclusionORSet:
    """The ops an explicit edit (revert/pin, U8) removed from a ref's ideal, as an OR-Set (1.1):
    each exclusion `add` carries a globally-unique tag, a re-include (restore/cherry-pick, or the
    undo of a revert) tombstones the tags it locally observes, and the *live* exclusion set is every
    op-id with at least one non-tombstoned tag. This is the *positive* record that makes a revert
    durable: `ideal(ref) = reduce(provenance-in-ancestry − exclusions.live())`, so a later git
    history rewrite that re-mines the same content under a new sha (rebase/cherry-pick) can no
    longer resurrect a reverted op through provenance -- the exclusion subtracts it regardless
    (F11/F20). Mirrors `DeclaredORSet`; per-ref, local (never travels -- that is 1.2/Phase 4)."""

    adds: frozenset[tuple[str, str]] = frozenset()  # (op_id, tag)
    tombstones: frozenset[str] = frozenset()  # tombstoned tags

    def live(self) -> frozenset[str]:
        """Every op-id that still has an un-tombstoned exclusion tag -- what the ideal subtracts."""
        dead = self.tombstones
        return frozenset(oid for (oid, tag) in self.adds if tag not in dead)

    def head(self) -> str:
        """A stable content fingerprint of the live exclusion set, for change detection."""
        return hashlib.sha256(",".join(sorted(self.live())).encode()).hexdigest()[:16]

    def union(self, other: ExclusionORSet) -> ExclusionORSet:
        """Merge two clones' OR-Sets by tag (Phase 1.2 §E), exactly like `DeclaredORSet.union`: a
        concurrent revert (an add carrying a tag the other side never saw) survives, and a restore
        (a tombstone) travels. Never merges by bare op-id -- that is what makes the shared exclusion
        log a CRDT rather than a lossy last-writer-wins set."""
        return ExclusionORSet(self.adds | other.adds, self.tombstones | other.tombstones)


def _exclusion_from_body(body: dict | None) -> ExclusionORSet:
    if body is None:
        return ExclusionORSet()
    return ExclusionORSet(
        adds=frozenset((oid, tag) for oid, tag in body.get("adds", [])),
        tombstones=frozenset(body.get("tombstones", [])),
    )


def load_exclusions(repo: Path) -> dict[str, ExclusionORSet]:
    """The per-ref exclusion OR-Sets: `{ref_key: ExclusionORSet}`. Absent file loads as `{}`."""
    raw = state.load_json(repo, "exclusions", default={})
    return {key: _exclusion_from_body(body) for key, body in raw.items()}


def exclusions_at(gb: GitBinding, sha: str) -> dict[str, ExclusionORSet]:
    """A teammate's per-ref exclusion OR-Sets as committed on `refs/sgt/state` at `sha` -- the
    historical-blob read `resolve` unions by tag (Phase 1.2 §E). Absent blob yields `{}`."""
    raw = state.load_blob_json(gb, sha, "exclusions")
    if raw is None:
        return {}
    return {key: _exclusion_from_body(body) for key, body in raw.items()}


def save_exclusions(repo: Path, table: dict[str, ExclusionORSet]) -> None:
    state.save_json(repo, "exclusions", {
        key: {
            "adds": sorted([oid, tag] for oid, tag in orset.adds),
            "tombstones": sorted(orset.tombstones),
        }
        for key, orset in table.items()
    })


def merge_exclusions(
    ours: dict[str, ExclusionORSet], theirs: dict[str, ExclusionORSet]
) -> dict[str, ExclusionORSet]:
    """Per-ref-key union of two exclusion tables (Phase 1.2 §E): each key's OR-Set unions by tag, so
    a key only one side carries is kept verbatim. Detached-HEAD sha-keys union as harmless clone-local
    noise -- there is nothing to reconcile between two clones' distinct detached shas."""
    merged = dict(ours)
    for key, orset in theirs.items():
        merged[key] = merged[key].union(orset) if key in merged else orset
    return merged


def _load_backfill_state(repo: Path) -> dict[str, dict]:
    """The persisted per-ref genesis-backfill frontier: `{ref_key: {"genesis_frontier": ...,
    "reached_genesis": ...}}`. Local, never travels -- like `_load_witnesses`, an absent file
    loads as `{}`."""
    return state.load_json(repo, "backfill", default={})


def _save_backfill_state(repo: Path, table: dict[str, dict]) -> None:
    state.save_json(repo, "backfill", table)


def _load_sync_cache(repo: Path) -> dict[str, dict]:
    """The per-ref no-op gate: `{ref_key: {"fp": <fingerprint>, "ids": [sorted op_ids]}}`. `fp`
    covers everything a re-mine depends on -- HEAD, the dirty working-tree source content, and the
    persisted ideal entry -- so when it's unchanged since the last `_sync`, that sync would return
    the same ideal and can be skipped entirely (the mine's O(files) dirty pass is the bulk of a
    warm `get()`). Local, never travels; an absent file loads as `{}`."""
    return state.load_json(repo, "sync_cache", default={})


def _save_sync_cache(repo: Path, table: dict[str, dict]) -> None:
    state.save_json_if_changed(repo, "sync_cache", table)


def _sync_fingerprint(gb: GitBinding, head: str, ideal_entry) -> str | None:
    """The fingerprint the no-op gate compares. None (git couldn't compute the dirty digest) means
    'don't gate -- mine'. `ideal_entry` is the persisted ideal id-list for this ref, so an explicit
    ideal edit (revert/pin, U8) -- which moves neither HEAD nor the tree -- still changes the
    fingerprint and forces a fresh sync."""
    digest = gb.dirty_source_digest()
    if digest is None:
        return None
    h = hashlib.sha256()
    h.update(head.encode())
    h.update(b"\x00")
    h.update(digest.encode())
    h.update(b"\x00")
    h.update(json.dumps(ideal_entry, sort_keys=True).encode())
    return h.hexdigest()


def cached_map_is_current(repo: str | Path) -> bool:
    """True when a `get()` would short-circuit on `_sync`'s no-op gate -- i.e. HEAD, the working-tree
    source, and the persisted ideal are all unchanged since the last sync, so a cached read (`sgt
    log` without `--refresh`) already reflects reality and there is nothing for `--refresh` to pick
    up. Mirrors the gate below (the `prev_head == head` + fingerprint + cached-ids check) but never
    mines: it only runs the same O(files) dirty digest the gate itself compares. Any real change --
    a new commit, an edited/added source file, or an explicit ideal edit -- moves the fingerprint
    and returns False. Conservative on any ambiguity (git can't digest, mid-backfill, cached ids no
    longer in the store): returns False, so we err toward a spurious refresh hint over falsely
    claiming freshness."""
    repo = Path(repo)
    try:
        gb = GitBinding(repo)
        head = gb.head()
        key = _ref_key(gb) or head
        if _load_witnesses(repo).get(key) != head:
            return False
        backfill = _load_backfill_state(repo).get(key)
        if backfill is not None and not backfill.get("reached_genesis", False):
            return False
        fp = _sync_fingerprint(gb, head, _load_ideal_table(repo).get(key))
        cached = _load_sync_cache(repo).get(key)
        if fp is None or cached is None or cached.get("fp") != fp:
            return False
        return frozenset(cached.get("ids", [])) <= {op.id for op in opindex.index_ops(repo)}
    except Exception:
        return False


def _load_ideal_journal(repo: Path) -> dict[str, list[dict]]:
    """The per-ref undo stack: `{ref_key: [{ideal: [op_ids], witness: sha}, ...]}` -- the prior
    ideals `record_ideal` pushed before each overwrite (U26). Local, never travels."""
    return state.load_json(repo, "ideal_journal", default={})


def _save_ideal_journal(repo: Path, journal: dict[str, list[dict]]) -> None:
    state.save_json(repo, "ideal_journal", journal)


Edge = tuple[str, str]  # (a, b) meaning a <= b


@dataclass(frozen=True)
class DeclaredORSet:
    """The declared order edges as an OR-Set (U21/D6): each `add` carries a globally-unique tag, a
    `remove` tombstones the tags it locally observes, and the *live* edge set is every edge value
    with at least one non-tombstoned tag. This is what makes a retraction durable, travelling state
    -- a concurrent add elsewhere (a tag this clone never saw) survives the retraction, and a
    resolved retraction stays resolved after sync, unlike the pre-U21 flat G-Set which could only
    ever grow. Two clones' OR-Sets merge by tag (`union`), never by bare edge value."""

    adds: frozenset[tuple[str, str, str]] = frozenset()  # (a, b, tag)
    tombstones: frozenset[str] = frozenset()  # tombstoned tags

    def live(self) -> frozenset[Edge]:
        """Every edge value that still has an un-tombstoned tag -- what `order` actually consumes."""
        dead = self.tombstones
        return frozenset((a, b) for (a, b, tag) in self.adds if tag not in dead)

    def union(self, other: DeclaredORSet) -> DeclaredORSet:
        return DeclaredORSet(self.adds | other.adds, self.tombstones | other.tombstones)


def _orset_from_body(body: dict | None) -> DeclaredORSet:
    if body is None:
        return DeclaredORSet()
    return DeclaredORSet(
        adds=frozenset((a, b, tag) for a, b, tag in body.get("adds", [])),
        tombstones=frozenset(body.get("tombstones", [])),
    )


def load_declared_orset(repo: Path) -> DeclaredORSet:
    """The declared-edge OR-Set from the working tree (empty when the file doesn't exist yet)."""
    return _orset_from_body(state.load_json(repo, "declared_orset"))


def declared_orset_at(gb: GitBinding, sha: str) -> DeclaredORSet:
    """A teammate's declared-edge OR-Set as committed at `sha` -- the historical-blob read `sync`
    unions by tag."""
    return _orset_from_body(state.load_blob_json(gb, sha, "declared_orset"))


def save_declared_orset(repo: Path, orset: DeclaredORSet) -> None:
    """Persist the OR-Set."""
    state.save_json(repo, "declared_orset", {
        "adds": sorted([a, b, tag] for a, b, tag in orset.adds),
        "tombstones": sorted(orset.tombstones),
    })


def declare_after(repo: Path, a: str, b: str) -> None:
    """`sgt after a b`: add the edge `a <= b` with a fresh, globally-unique tag (OR-Set add)."""
    from sgt.core import oplog

    snap = oplog.snapshot(repo, ["declared_orset"])  # inverse: the OR-Set before the add
    # Record the inverse for `undo` (U8) *before* mutating, so a failed edge write is still
    # recoverable. Best-effort like the D1 land log: `after` runs on repos that may lack a HEAD/ref
    # (a bare `.sgt` dir in a unit test), so a failed append must never break the edge add.
    try:
        oplog.append(repo, {"kind": "after", "snapshot": snap, "edge": [a, b]})
    except Exception:  # noqa: BLE001 -- provenance logging is never load-bearing for the mutation
        pass
    orset = load_declared_orset(repo)
    save_declared_orset(repo, orset.union(DeclaredORSet(adds=frozenset({(a, b, uuid.uuid4().hex)}))))


def retract_after(repo: Path, a: str, b: str) -> frozenset[str]:
    """`sgt after --retract a b`: tombstone every tag *currently observed locally* for edge
    `(a, b)` (OR-Set remove). A concurrent add elsewhere, with a tag this clone hasn't seen, is not
    tombstoned and survives the sync -- the whole point of OR-Set over a blanket delete. Returns the
    set of tags tombstoned (empty if the edge wasn't declared here)."""
    orset = load_declared_orset(repo)
    observed = frozenset(tag for (x, y, tag) in orset.adds if (x, y) == (a, b))
    save_declared_orset(repo, DeclaredORSet(adds=orset.adds, tombstones=orset.tombstones | observed))
    return observed


def _load_declared(repo: Path) -> frozenset[Edge]:
    """The *live* declared order edges (`sgt after a b` -> `(a, b)` meaning `a <= b`, U8's escape
    hatch for ordering the analyzer can't infer) -- the OR-Set resolved down to the plain edge set
    every consumer (`order`'s validity + up/down-sets, `sync`'s cycle detection) expects. Repo-
    global, not per-ref: an edge is a fact about two ops' content, independent of the checked-out
    ref."""
    return load_declared_orset(repo).live()


def _ref_key(gb: GitBinding) -> str | None:
    """This ref's stable key in the witness table: its symbolic name, or the raw HEAD sha in
    detached-HEAD state (each detached position tracked independently)."""
    return gb.symbolic_ref() or gb.head()


def current_ref_key(repo: str | Path) -> str | None:
    """The current ref's key in the per-ref local tables (witness/ideal/fidelity) -- a thin public
    wrapper over `_ref_key` for callers outside this module (`sgt.api.grid_view`, the fidelity
    writer) that need to read or write the row belonging to whatever is checked out now."""
    return _ref_key(GitBinding(Path(repo)))


def _committed_ids_by_provenance(gb: GitBinding, store: Store) -> set[str]:
    """Every stored op whose provenance intersects this ref's own commit ancestry -- the ref's
    ideal derived fresh from content-addressed history. Used only to *seed* the persisted
    `.sgt/local/ideal.json` entry on a ref's first tracked `get()`; once that entry exists it is
    authoritative, so an explicit ideal edit (revert/pin, U8) is never silently re-derived away
    by a later provenance scan that has no way to represent "excluded though still in history".

    Reduced to a valid ideal (U20/U22.5): once a sync surfaces a fork, *both* forked tips ride the
    ref's ancestry (theirs' side is the merge's second parent), and real single-clone history alone
    already produces forks (add/delete/re-add rebirths `(symbol, None)` twice) and ungrounded ops (a
    predecessor squashed away), so a raw provenance scan is not directly a valid ideal --
    `order.reduce_to_ideal` grounds it and drops forked up-sets; forked tips live only in
    `.sgt/forks.json`, never a verb-visible ideal (D5)."""
    ref_commits = set(gb.commit_shas())
    all_ops = opindex.index_ops(store.repo)
    included = {op.id for op in all_ops if set(op.provenance) & ref_commits}
    return set(order.reduce_to_ideal(included, all_ops))


def _record_parked_forks(repo: Path, parked: list[tuple[str, str, str]]) -> None:
    """1.4: union the forks this rebuild parked into the one shared `.sgt/forks.json` store so
    `sgt forks`/`resolve` surface them. Additive -- it never clears a record `land`'s refusal (F23)
    or a teammate's `sync` flush wrote; a fork leaves the store only when its resolution drops it
    from the rebuild and a replacing writer rewrites, or `resolve` removes it (the CRDT-tombstone
    lifecycle is Phase 4). Routes through the single `save_fork_records` writer (never a bespoke
    `save_json`) and writes only when the union gains something, so a re-`get()` over the same fork
    touches no mtime (R5). Its own lock guards the read-modify-write against a concurrent `sync`
    flush; `_ensure_fidelity`'s own lock is the precedent -- `locked_section` is non-reentrant (U23),
    so this must run *outside* the checkpoint section. `materialize` imports `lens` at module load,
    so the reverse import is lazy to avoid a cycle."""
    if not parked:
        return
    from sgt.core.sync import materialize

    with locked_section(repo):
        existing = {
            (r["symbol"], r["tips"][0], r["tips"][1])
            for r in state.load_json(repo, "forks", default=[])
        }
        merged = existing | set(parked)
        if merged != existing:
            materialize.save_fork_records(repo, tuple(sorted(merged)))


def _fidelity_fp(committed_ids) -> str:
    """A stable fingerprint of a ref's committed ideal -- the key the fidelity marks are cached
    against, so a stale entry is detected whenever the ideal moved (a new commit, a fork surfaced,
    a fork resolved) regardless of *which* verb moved it."""
    return hashlib.sha256(",".join(sorted(committed_ids)).encode()).hexdigest()[:16]


def _ensure_fidelity(repo: Path, gb: GitBinding, key: str, committed_ids, all_ops: list) -> None:
    """Keep this ref's mining-fidelity marks current (R6/U2): the commits whose ops
    `order.reduce_to_ideal` had to drop from the ideal -- a fork tip, or an op whose chain
    predecessor is off this ref -- so `grid_view` marks them "partial" instead of silently omitting
    the loss. The dropped set is `included \\ reduce_to_ideal(included)` over the ref's *raw*
    provenance union, so it isolates a genuine reconstruction loss from an intentional user edit:
    a revert removes an op from the persisted ideal but not from `included`, so it is never mistaken
    for a fidelity mark.

    Cached against the committed ideal's fingerprint: a no-op when the ideal is unchanged (a hash +
    a small JSON read), so the warm path pays nothing. The full-store `reduce_to_ideal` -- the ~28s
    large-store cost U8/U9 optimize -- runs only when the ideal actually moved (or the entry is
    absent), never on a glance."""
    fp = _fidelity_fp(committed_ids)
    entry = state.load_json(repo, "fidelity", default={}).get(key)
    if isinstance(entry, dict) and entry.get("ideal_fp") == fp:
        return  # marks already current for this exact ideal
    ref_commits = set(gb.commit_shas())
    included = {op.id for op in all_ops if set(op.provenance) & ref_commits}
    reduced = set(order.reduce_to_ideal(included, all_ops))
    by_id = {op.id: op for op in all_ops}
    dropped_shas = sorted({sha for oid in (included - reduced) for sha in by_id[oid].provenance})
    with locked_section(repo):
        table = state.load_json(repo, "fidelity", default={})
        table[key] = {"ideal_fp": fp, "shas": dropped_shas}
        state.save_json(repo, "fidelity", table)


def ops_with_frontier_images(repo: str | Path, ideal: Ideal) -> list:
    """The footprint-only index ops, with the *frontier producers'* full ops (images included)
    substituted in -- exactly the set `fold.code` reads images from when materializing `ideal`.
    `Store.all_ops()` decodes every op's images (85%+ of the store's bytes) yet a materializing
    read only ever opens the images of the ops at the ideal's frontier, so a view that needs
    `code(ideal, ops)` plus footprint-level queries can use this and skip the full decode. Do
    NOT hand the result to anything folding a *different* ideal -- its frontier ops would carry
    `images={}` (empty, not absent) and fold to zero-length content."""
    from concurrent.futures import ThreadPoolExecutor

    repo = Path(repo)
    ops = opindex.index_ops(repo)
    need = sorted(set(ideal.frontier(ops).values()))
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
    return Ideal.from_ops(ids, opindex.index_ops(repo))


def ideal_for_ref(repo: str | Path, ref: str = "HEAD", store: Store | None = None) -> Ideal:
    """The ideal a given ref's committed history implies -- a *pure read*: no mining, no
    checkout, no side effects. It projects the ops already in the store onto `ref`'s own commit
    ancestry, exactly as `_committed_ids_by_provenance` does for the current ref, but for any
    ref, and never consults the persisted `.sgt/local/ideal.json` table (see `_sync`). A ref
    whose history was never mined yields an under-approximated ideal, so contact it with `get()`
    first for completeness. The read views (U7's `state_view`/`ideal_diff_view`) use this to
    inspect and compare refs without disturbing the working tree.

    `store` is accepted (unused) for call-site compatibility -- this is a pure ideal-derivation,
    so it reads the footprint-only `opindex` sidecar (never `op.images`) rather than a caller-
    supplied `Store`."""
    repo = Path(repo)
    gb = GitBinding(repo)
    ref_commits = set(gb.commit_shas(ref))
    all_ops = opindex.index_ops(repo)
    included = {op.id for op in all_ops if set(op.provenance) & ref_commits}
    return Ideal.from_ops(order.reduce_to_ideal(included, all_ops), all_ops)


def _sync(repo: Path, treat_as_root: str | None = None) -> Ideal:
    gb = GitBinding(repo)
    store = Store(repo)
    store.init()

    head = gb.head()
    if head is None:
        # Bootstrap (R9 from day zero): a repo with no commits yet can still carry mineable source
        # in an untracked/dirty working tree -- that is exactly the state a fresh `sgt init` leaves,
        # and the first `sgt save` must be able to capture it into the first witness commit. The
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
    backfill_table = _load_backfill_state(repo)
    has_backfill_record = key in backfill_table
    backfill_state = backfill_table.get(key, {"genesis_frontier": None, "reached_genesis": False})
    new_witness = prev_head
    new_backfill_state = dict(backfill_state)

    # No-op gate (KTD-perf): mine-on-contact (R9) exists so a read reflects reality, but re-mining a
    # tree that hasn't changed since the last `_sync` reproduces the same ops -- and the dirty pass
    # is O(files), the bulk of a warm `get()`. So when we're forward-current (`prev_head == head`),
    # not mid-backfill, and the fingerprint (HEAD + dirty source content + persisted ideal) matches
    # the last sync, return the cached ideal without touching git history, the entity graph, or the
    # store. Any real change -- a new commit, an edited/added source file, or an explicit ideal edit
    # (revert/pin) -- moves the fingerprint and falls through to a full sync, so R9's guarantee holds
    # exactly where it matters.
    backfill_in_progress = has_backfill_record and not backfill_state.get("reached_genesis", False)
    if treat_as_root is None and prev_head is not None and prev_head == head and not backfill_in_progress:
        ideal_entry = _load_ideal_table(repo).get(key)
        fp = _sync_fingerprint(gb, head, ideal_entry)
        cached = _load_sync_cache(repo).get(key)
        if fp is not None and cached is not None and cached.get("fp") == fp:
            # Fidelity (U2): the warm no-op path skips the whole sync body, so refresh the marks
            # here too -- keyed on the cached ideal's fingerprint, so this is a cheap no-op unless
            # the ideal actually moved since the marks were last computed (e.g. a `sync` that
            # surfaced a fork updated the ideal but not through this function). No re-mining.
            cached_ids = frozenset(cached.get("ids", []))
            index_ops = opindex.index_ops(repo)
            # The fingerprint covers HEAD, the working tree, and the persisted ideal entry -- but
            # NOT the `.sgt/ops` store, which a `git switch` can swap underneath us: ops committed
            # on one branch and absent on another are removed by the checkout, while the gitignored
            # ideal table (and this cache) survive it and keep pointing at the vanished op ids. That
            # leaves the gate's HEAD/tree/ideal-entry fingerprint unchanged, so a blind short-circuit
            # would hand back a cached ideal whose ops are no longer materialized -- `Ideal.from_ops`
            # then rejects it. Only gate when every cached id is still in the index; otherwise fall
            # through to a full sync, which re-mines the ref and reduces the stale set to what survives.
            if cached_ids <= {op.id for op in index_ops}:
                _ensure_fidelity(repo, gb, key, cached_ids, index_ops)
                return Ideal.from_ops(cached_ids, index_ops)

    # The dirty pass mines a virtual pending commit -- a full working-tree snapshot + whole-tree
    # entity graph -- so it costs O(files) even when nothing changed. Skip it unless some non-
    # `.sgt/` path actually differs from HEAD (R16); on a tree whose only churn is `.sgt/` state
    # it would rebuild the whole graph only to produce no source ops. `mine()` additionally skips
    # it whenever a chunk's own deadline cuts the history loop short (U1), so passing the caller's
    # true intent here -- even on a backward chunk -- is safe: it only actually runs once some
    # chunk finishes its historical work inside budget.
    # An in-progress git merge/cherry-pick/revert is the one case we refuse to mine the working tree
    # (F26): its conflict-marker bytes would become a permanent op. Skip the dirty pass entirely
    # while one is live -- reads still return the committed ideal, and the resolved tree is mined on
    # the next contact once the operation finishes. `save` refuses outright with a fix-it message.
    include_dirty = gb.has_dirty_source() and merge_in_progress(gb) is None

    # (1, 2) Decide this call's one chunk (KTD-1/KTD-2). Each branch below mines a single,
    # deadline-bounded piece of history; which piece depends on where this ref's witness and
    # genesis-backfill frontier currently stand.
    if treat_as_root is not None:
        # R10 genesis-horizon (`init(horizon=...)`): unbounded and unchunked, exactly as before --
        # a horizon seals its boundary permanently at `init` time, so this ref never backfills.
        since = gb.parent_of(treat_as_root)
        mined_ops, _last_sha = mine(repo, since=since, treat_as_root=treat_as_root, include_dirty=include_dirty)
        new_witness = head
        new_backfill_state = {"genesis_frontier": None, "reached_genesis": True}
    elif prev_head is None:
        # True first contact: there is no earlier witness to catch up from, so this chunk walks
        # backward from `head` instead (U2). Bootstraps `witness=head` at this same checkpoint --
        # never via a separate forward mine -- so the ref is immediately "forward current" and
        # every later call on it is pure backward backfill until `reached_genesis`.
        deadline = time.monotonic() + _CHUNK_BUDGET_SECONDS
        window = gb.history_backward(head)
        mined_ops, last_sha = mine(repo, history_override=window, deadline=deadline, include_dirty=include_dirty)
        new_witness = head
        new_backfill_state = {
            "genesis_frontier": last_sha,
            "reached_genesis": last_sha is not None and gb.parent_of(last_sha) is None,
        }
    elif prev_head == head and has_backfill_record and not backfill_state.get("reached_genesis", False):
        # Already forward-current, and a backward walk is actually in progress for *this* key:
        # continue it one deadline-bounded window past where the last chunk stopped. Gated on
        # `has_backfill_record` (not just `reached_genesis`) so a witness planted by `record_ideal`
        # (as `put()` does for its own materialize commit, or as a detached HEAD's per-commit
        # `_ref_key` fallback does on every new commit, KTD-3 follow-up) -- which never seeds a
        # backfill entry for its key -- falls through to the ordinary catch-up branch below instead
        # of mistaking "no entry" for "needs a fresh genesis walk" and re-mining history that was
        # already fully accounted for under the ref's previous key.
        deadline = time.monotonic() + _CHUNK_BUDGET_SECONDS
        frontier = backfill_state.get("genesis_frontier")
        start = head if frontier is None else gb.parent_of(frontier)
        window = gb.history_backward(start) if start is not None else []
        mined_ops, last_sha = mine(repo, history_override=window, deadline=deadline, include_dirty=include_dirty)
        if last_sha is not None:
            new_backfill_state = {"genesis_frontier": last_sha, "reached_genesis": gb.parent_of(last_sha) is None}
    else:
        # Ordinary forward catch-up: mine whatever landed on `head` since the last witness.
        # Prioritized over backward backfill whenever the two compete (a ref mid-backfill that
        # also gains new commits catches up forward first). Also the terminal steady state
        # (`prev_head == head`, `reached_genesis` already `True`) -- an empty range that still
        # carries the dirty pass, so `get()` keeps absorbing working-tree edits (R9) forever, not
        # just until backfill finishes.
        deadline = time.monotonic() + _CHUNK_BUDGET_SECONDS
        mined_ops, _last_sha = mine(repo, since=prev_head, target=head, deadline=deadline, include_dirty=include_dirty)
        new_witness = head

    # (3) Persist each mined op. Partition by the *returned* (post-merge) op's provenance, not the
    # mined op's: a dirty edit whose content is byte-identical to something already committed
    # comes back from `store.add` as the existing op with its real provenance intact, so it
    # rightly counts as committed, not pending.
    # Staleness must be judged *before* our own `store.add` writes below: those writes always
    # make the snapshot look stale (new dirents / bumped mtimes), which used to force the full
    # `rebuild` -- an every-op-file re-read -- on every mining sync, leaving `apply_delta`
    # unreachable. Checked here, "stale" means someone *else* wrote ops the snapshot missed
    # (rebuild is right); "fresh" means the snapshot covers the pre-mine store exactly, so
    # upserting just the ops this sync touched reproduces a complete snapshot. `store.add` takes
    # its own lock per op, so these adds cannot move inside the `locked_section` below (that would
    # nest the store lock, violating the U23 contract) -- hence the check is unavoidably outside
    # it. Residual (accepted, same class as `opindex._ops_dir_stat`'s same-tick note): if a
    # concurrent writer's provenance-merge rewrites an existing op file (count-neutral mtime bump)
    # after this check and then crashes before its own `apply_delta`, our `apply_delta` stamps a
    # newer `built_mtime_ns` that masks the rewrite until the next unrelated op-store write. Needs
    # a concurrent writer plus a crash in that window; closing it would cost a per-op-file mtime
    # re-scan on this hot path for no steady-state benefit.
    index_stale_before_add = opindex.is_stale(repo)

    new_committed_ids: set[str] = set()
    pending_ids: set[str] = set()
    stored_ops = []
    for op in mined_ops:
        stored = store.add(op)
        stored_ops.append(stored)
        (new_committed_ids if stored.provenance else pending_ids).add(stored.id)

    # Keep the footprint-only opindex sidecar current: a full rebuild pays the images decode once
    # (cheaper than letting every read view re-derive staleness against a snapshot already known
    # stale), otherwise an incremental upsert of just the ops this sync touched. Locked (R5/R6):
    # apply_delta's read-modify-write would otherwise lose a concurrent _sync's own update --
    # self-healing on the next read via is_stale's dirent-count check, but the lock avoids the
    # transient loss entirely. Ops were added above, before this section, so `Store.add`'s own
    # lock never nests inside this one (U23 / locked_section contract).
    with locked_section(repo):
        if index_stale_before_add:
            opindex.rebuild(repo, store)
        else:
            opindex.apply_delta(repo, stored_ops)

    # Seed the persisted ideal from a provenance scan the first time this ref is tracked; thereafter
    # the stored set is the base the sync builds on -- it carries committed ops that carry no
    # provenance yet (`put` commits via `Sgt-Op:` trailers and advances the witness without re-mining
    # them, so their provenance stays empty until a later cold mine), which a raw provenance scan
    # would drop.
    all_ops = opindex.index_ops(repo)
    ideal_table = _load_ideal_table(repo)
    already_seeded = key in ideal_table
    base_ids = set(ideal_table[key]) if already_seeded else _committed_ids_by_provenance(gb, store)

    # `exclusions` is the per-ref, append-only OR-Set of ops an explicit edit (revert/pin, U8)
    # removed -- a *positive* record subtracted from the ideal (1.1). It is what makes a revert
    # durable across a git history rewrite: a rebase/cherry-pick that re-mines the same content under
    # a new sha re-adds the op via `new_committed_ids`, but the exclusion subtracts it right back out,
    # so it can no longer silently resurrect (F11/F20). Before, the union-only reconciliation had no
    # way to represent "excluded though back in history" and the op came back.
    exclusions_table = load_exclusions(repo)
    if already_seeded and key not in exclusions_table:
        # Migrate an existing repo whose reverts were recorded only as *absences* in the base set
        # into explicit exclusions, so the switch to exclusion-subtracted ideals does not resurrect a
        # pre-existing revert on the first history rewrite. A genuine revert is an op that would
        # *survive* reduction from pure history (`_committed_ids_by_provenance`) yet is absent from
        # the base set -- so `reduce(provenance) − base` isolates reverts from reduction-drops (fork
        # tips, ungrounded ops), which must stay out of the exclusion set or excluding one fork tip
        # would silently un-fork the other. `new_committed_ids` (ops legitimately mined *into* the
        # ideal this sync, e.g. a foreign hotfix) are excluded from the seed too. Runs once per ref.
        implied = _committed_ids_by_provenance(gb, store) - base_ids - new_committed_ids
        if implied:
            exclusions_table[key] = ExclusionORSet(
                adds=frozenset((oid, uuid.uuid4().hex) for oid in implied)
            )
            save_exclusions(repo, exclusions_table)
    exclusions = exclusions_table.get(key, ExclusionORSet())

    # The durable ideal gains newly-committed ops and loses excluded ones; the dirty overlay is never
    # persisted, so a discarded working-tree edit (e.g. `git checkout -- .`) simply stops appearing
    # on the next `get()`. Reduce to a valid ideal *before* persisting (U22.5): real history mined
    # cold contains add/delete/re-add forks and predecessors squashed out of this ref, so the raw
    # union is not directly constructible -- persisting it unreduced would leave an invalid
    # `.sgt/local/ideal.json` on disk and then raise, corrupting the table.
    seed = (base_ids | new_committed_ids) - exclusions.live()
    committed_ids = set(order.reduce_to_ideal(seed, all_ops))

    # (4) Checkpoint: the witness, the ideal table, and the backfill state must each land
    # atomically whenever any of them changed (R5, widened to a triple by this unit) -- a crash
    # that moved one without the others would make the next call trust stale state. One locked
    # section; only the tables that actually changed get rewritten, so a no-op read still never
    # touches a `.sgt/**/*.json` mtime a client's file watcher would react to. A backward-only
    # chunk can advance `genesis_frontier` while leaving the witness and the reduced ideal set
    # untouched, and that alone must still persist here -- otherwise backfill progress silently
    # fails to checkpoint. Ops were added above, before this section, so `Store.add`'s own lock
    # never nests inside this one (U23 / locked_section contract).
    ideal_changed = not already_seeded or committed_ids != base_ids
    witness_changed = new_witness != prev_head
    # A true-first-contact chunk (prev_head is None) that hits its deadline before mining even one
    # commit computes a `new_backfill_state` that coincidentally equals the never-seen-this-key
    # default -- persist it anyway, or `has_backfill_record` stays False forever and the next call
    # falls through to the no-op forward-catchup branch instead of resuming the backward walk.
    backfill_changed = new_backfill_state != backfill_state or (prev_head is None and not has_backfill_record)
    if ideal_changed or witness_changed or backfill_changed:
        with locked_section(repo):
            if ideal_changed:
                ideal_table[key] = sorted(committed_ids)
                _save_ideal_table(repo, ideal_table)
            if witness_changed:
                table = _load_witnesses(repo)
                table[key] = new_witness
                _save_witnesses(repo, table)
            if backfill_changed:
                backfill_table[key] = new_backfill_state
                _save_backfill_state(repo, backfill_table)

    # Fidelity marks (U2/R6): refresh the ref's marks against the current committed ideal. A cheap
    # fingerprint no-op unless the ideal moved. Outside the section above -- `_ensure_fidelity`
    # takes its own lock and `locked_section` is non-reentrant (U23).
    _ensure_fidelity(repo, gb, key, committed_ids, all_ops)

    # 1.4 (F7/F8): `reduce_to_ideal` parked every genuine same-symbol fork at its common ancestor by
    # dropping both tips -- `fork_free` did so *silently*, so `sgt forks`/`resolve` saw nothing to
    # reconcile. Surface them in the one shared fork store, exactly as sync/land do -- never silently
    # exclude. A fork can appear (a conflicting merge) while the *ideal set* stays put (the symbol
    # parks back at a version already in the ideal), so this hangs off the witness-moved full-run
    # path, not `ideal_changed`. `_record_parked_forks` no-ops the write unless the store's union
    # actually gains something, so a re-`get()` over the same fork touches no mtime.
    _record_parked_forks(repo, order.parked_forks(seed, all_ops))

    # (5) The in-memory ideal carries the dirty overlay on top of the durable committed set; a
    # dirty edit that forks committed state is dropped by the same reduction rather than crashing.
    result = Ideal.from_ops(order.reduce_to_ideal(committed_ids | pending_ids, all_ops), all_ops)

    # (6) Refresh the no-op gate's cache so the next `get()` on an unchanged tree short-circuits.
    # Only when we're in the stable state the gate checks for (forward-current, backfill complete):
    # caching mid-backfill would let the gate skip the remaining backward chunks. Recompute the
    # fingerprint against the *new* ideal entry (this sync may have changed it).
    reached_genesis = new_backfill_state.get("reached_genesis", False) or not (
        has_backfill_record or new_backfill_state.get("genesis_frontier") is not None
    )
    if treat_as_root is None and new_witness == head and reached_genesis:
        fp = _sync_fingerprint(gb, head, sorted(committed_ids))
        if fp is not None:
            table = _load_sync_cache(repo)
            table[key] = {"fp": fp, "ids": sorted(result.op_ids)}
            _save_sync_cache(repo, table)
    return result


def get(repo: str | Path) -> Ideal:
    """Mine what's new to the current ref, persist it, and return the ref's current ideal."""
    return _sync(Path(repo))


def sync_status(repo: str | Path, ref: str | None = None) -> dict:
    """A pure read (no mining) of how far `ref`'s sync has progressed: whether its witness has
    caught up to its head, and whether an in-progress genesis backfill (if any) has finished.
    `ref=None` reports on the currently checked-out ref, using the same key `_sync` would use for
    it. `complete` is `True` iff the witness equals head and any backfill has `reached_genesis`."""
    repo = Path(repo)
    gb = GitBinding(repo)
    if ref is None:
        key = _ref_key(gb) or gb.head()
        head = gb.head()
    else:
        head = gb.rev_parse(ref)
        key = ref if ref.startswith("refs/") else f"refs/heads/{ref}"
    witness = _load_witnesses(repo).get(key)
    backfill = _load_backfill_state(repo).get(key)
    reached_genesis = backfill is None or bool(backfill.get("reached_genesis"))
    complete = witness is not None and witness == head and reached_genesis
    return {"complete": complete, "reached_genesis": reached_genesis,
            "history_rewritten": _history_rewritten(gb, witness, head)}


def dropped_ideal_ops(repo: str | Path, ref: str | None = None) -> list[str]:
    """Ops in this ref's *persisted* ideal whose witnessing commits are all gone from git history --
    the residue of a backward/sideways move (`git reset --hard`, `commit --amend`, `branch -f`).

    This is the P0-1 desync made observable. `_sync` seeds `base_ids` from `ideal_table[key]` and
    treats it as authoritative from then on (deliberately: that is what keeps an explicit revert
    durable, F11/F20), but it never intersects it with the ref's *current* ancestry. So after a
    backward move the ideal still names ops from dropped commits: `log --summary` counts files that
    no longer exist, `--map` shows vanished symbols, and a later `save` can dead-end.

    Comparing the recorded *witness* to HEAD does not detect this -- mine-on-contact advances the
    witness to the new head while leaving the ideal untouched, so by the time any surface reads,
    witness == head and the ideal is still stale. The reliable signal is per-op reachability.

    Ops with *empty* provenance are never counted: `put` commits via `Sgt-Op:` trailers and advances
    the witness without re-mining, so a legitimately-live op can carry no provenance until a later
    cold mine. Only an op that names commits, none of which git can still reach, is definitively
    dropped -- which is also exactly the set a future auto-intersect on the catch-up branch may
    subtract, so the two agree by construction.

    A pure read: decodes the footprint-only index and one `git log`. Returns sorted op-ids."""
    repo = Path(repo)
    gb = GitBinding(repo)
    if ref is None:
        key = _ref_key(gb) or gb.head()
    else:
        key = ref if ref.startswith("refs/") else f"refs/heads/{ref}"
    recorded = _load_ideal_table(repo).get(key)
    if not recorded:
        return []
    reachable = set(gb.commit_shas())
    if not reachable:
        return []
    by_id = {op.id: op for op in opindex.index_ops(repo)}
    dropped = []
    for op_id in recorded:
        op = by_id.get(op_id)
        if op is None or not op.provenance:
            continue  # unknown, or committed-by-trailer with no provenance yet -- not evidence
        if not set(op.provenance) & reachable:
            dropped.append(op_id)
    return sorted(dropped)


def _history_rewritten(gb: GitBinding, witness: str | None, head: str | None) -> bool:
    """True iff this ref's recorded ideal still names ops from commits git can no longer reach --
    i.e. git history moved backward/sideways and `sgt advanced resync` is the remedy.

    Before this, the desync presented as ordinary working-tree drift, so every surface suggested
    `sgt save` -- which finds nothing new, prints "nothing to save", exits 0, and leaves the
    discrepancy in place. A remedy that reports success without fixing anything is worse than none,
    because it also removes the user's reason to keep looking."""
    if head is None:
        return False
    try:
        return bool(dropped_ideal_ops(gb.repo))
    except Exception:  # noqa: BLE001 -- a pure read feeding orientation; never fail a status call
        return False


def resync(repo: str | Path, *, reseed: bool = False) -> dict:
    """`sgt resync`: recover the current ref's ideal after a git history rewrite (`git reset --hard`,
    `commit --amend`, `branch -f`) desynced it. A backward/divergent move leaves the persisted
    `.sgt/local/ideal.json` still naming ops from now-dropped commits: `log`/`--map` show vanished
    symbols and a later `save` can dead-end. The old workaround was a manual `rm .sgt/local/ideal.json`
    (which blows away *every* ref). This drops just the current ref's derived local state -- witness,
    ideal, backfill frontier, sync cache -- then re-mines, so the ideal is re-derived from what is
    actually reachable from HEAD now. Explicit reverts (the exclusion set) are preserved so they stay
    durable across the rewrite (F11/F20); `--reseed` additionally clears them for a total reset to
    whatever git history says. The op store itself is append-only and never touched."""
    repo = Path(repo)
    gb = GitBinding(repo)
    key = _ref_key(gb) or gb.head()
    if key is None:
        return {"key": None, "before": 0, "after": 0, "reseed": reseed}

    before = len(_load_ideal_table(repo).get(key, []))
    with locked_section(repo):
        for load, save in (
            (_load_witnesses, _save_witnesses),
            (_load_ideal_table, _save_ideal_table),
            (_load_backfill_state, _save_backfill_state),
            (_load_sync_cache, _save_sync_cache),
        ):
            table = load(repo)
            if key in table:
                del table[key]
                save(repo, table)
        if reseed:
            excl = load_exclusions(repo)
            if key in excl:
                del excl[key]
                save_exclusions(repo, excl)

    ideal = get(repo)  # re-derive from current git reality (first-contact seed via provenance scan)
    return {"key": key, "before": before, "after": len(ideal.op_ids), "reseed": reseed}


def init(repo: str | Path, horizon: str | None = None) -> Ideal:
    """`sgt init`: bind (or reuse) the repo and the kernel store, then mine -- from genesis, or
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


def put(repo: str | Path, ideal: Ideal, message: str = "sgt: materialize ideal") -> str:
    """`code(I)` -> working tree -> a witness commit carrying one `Sgt-Op:` trailer per op the
    new tree embodies. Mine-before-materialize (R9): `get()` runs first so a dirty tree or a
    foreign commit is absorbed into the store, then the fold is refused (rather than silently
    clobbering) if it would overwrite an uncommitted change with different bytes."""
    repo = Path(repo)
    gb = GitBinding(repo)
    # A staged rewrite candidate deliberately leaves the tree dirty (U6). `put`'s `get()` would
    # re-mine those un-landed bytes and its fold would clobber them, committing a mixture -- so any
    # materializing edit refuses while a stage is live. `sgt land` commits the candidate directly
    # (`commit_materialized`, which does not call `get`); `sgt unstage` abandons it.
    if state.load_json(repo, "staged", default=None) is not None:
        raise DirtyWorkingTreeError(
            "a rewrite candidate is staged -- `sgt land` to commit it or `sgt unstage` to abandon "
            "it before another materializing edit"
        )
    get(repo)  # absorb any dirty tree / foreign commit first (R9)
    store = Store(repo)
    all_ops = store.all_ops()
    materialized = code(ideal, all_ops)
    conflicts = _dirty_conflicts(repo, gb, materialized)
    if conflicts:
        raise DirtyWorkingTreeError(
            f"put() would overwrite uncommitted changes: {sorted(conflicts)} "
            f"(if you just rewrote git history -- reset/amend/branch -f -- run `sgt advanced resync`)"
        )
    # Delta-scoped guard (Phase 0, 0.1): the fold rewrites *every* covered path, but this edit only
    # touches the symbols in `before_ideal Δ after_ideal`. A path outside that delta whose on-disk
    # bytes differ from what the ideal materializes is committed drift the fold would silently roll
    # back -- a local merge/cherry-pick the miner mis-attributed (F7/F9), or a foreign edit. Refuse
    # and name it rather than overwrite it; `_dirty_conflicts` cannot catch this because the drift
    # is committed (on-disk == HEAD), not an uncommitted change.
    delta_files = _delta_paths(current_ideal(repo).op_ids ^ ideal.op_ids, all_ops)
    drift = _outside_delta_drift(repo, materialized, delta_files)
    if drift:
        raise DirtyWorkingTreeError(
            "put() would roll back files outside this edit's scope, whose committed content "
            f"differs from sgt's recorded ideal: {sorted(drift)}"
        )
    _write_working_tree(repo, materialized, all_ops)
    # Phase 1.2: the op store and its tables no longer live in the branch tree, so the in-tree
    # `.sgt/ideal.json` recovery record (C5) is gone -- `sync`'s recovery ladder is log -> trailers
    # -> mine (the witness SHA still carries `Sgt-Op:` trailers, and a squash sgt never ran on
    # re-mines, coarser but LAW-0 reproducible). The local per-ref `ideal_table` cache stays
    # authoritative for the current ref.
    sha = gb.commit_all(message, trailers=format_op_trailers(sorted(ideal.op_ids)))
    # Publish the committed state (ops + tables) onto `refs/sgt/state` at `put`'s transaction
    # boundary, off the branch tree. Lazy import: the sync package pulls in lens, so a module-level
    # import would cycle; by call time every module is loaded.
    from sgt.core.sync import state_ref as _state_ref
    _state_ref.publish_from_local(gb, repo)
    return sha


def commit_materialized(repo: str | Path, ideal: Ideal, message: str) -> str:
    """Commit an `ideal` whose `code(I)` bytes are *already* on the working tree -- the rewrite
    staging path (U6). Unlike `put`, this neither re-mines the deliberately-dirty staged tree nor
    re-materializes: the staged bytes `stage` wrote are authoritative, so it just commits with the
    op trailers. The caller (`rewrite.land`) owns the staleness check that guarantees the tree still
    equals the staged candidate, so the commit can never capture a mixture. (Phase 1.2 removed the
    in-tree `.sgt/ideal.json` recovery write here too; see `put`.)"""
    repo = Path(repo)
    gb = GitBinding(repo)
    sha = gb.commit_all(message, trailers=format_op_trailers(sorted(ideal.op_ids)))
    from sgt.core.sync import state_ref as _state_ref  # lazy: avoid the sync<->lens import cycle
    _state_ref.publish_from_local(gb, repo)
    return sha


def record_ideal(
    repo: str | Path, ideal: Ideal, witness_sha: str, *, journal: bool = True,
    ref_key: str | None = None, record_exclusions: bool = True,
) -> None:
    """Persist an explicitly-edited `ideal` as the current ref's authoritative committed set and
    advance the ref's witness to `witness_sha` -- the durability an ideal-edit verb (U8's
    revert/pin/restore/cherry-pick) needs after `put()` commits. Without it, the next `get()`
    would re-mine the materializing commit's diff as a fresh op and union it back onto a stale
    base, undoing a reducing edit. Called *after* `put()` so `witness_sha` is the post-commit
    HEAD: the next `get()` then mines nothing new (`since == witness_sha`) and trusts this set.

    Before overwriting an existing entry it pushes the *outgoing* ideal (and its witness) onto the
    ref's undo stack, so `sgt undo` (U26) can restore exactly the ideal this edit replaced -- the
    edit history that lets undo be exact set arithmetic. `journal=False` suppresses that push (undo
    itself records with it off, so a second undo reaches the edge before the one just undone rather
    than toggling)."""
    repo = Path(repo)
    # `ref_key` lets `land` (U5) record under the *target* branch's key rather than the checked-out
    # ref -- landing a non-checked-out branch must advance that branch's table/witness, not HEAD's.
    key = ref_key if ref_key is not None else (_ref_key(GitBinding(repo)) or witness_sha)
    # The journal push, the table overwrite, and the witness advance are one read-modify-write:
    # holding the lock across all three closes the double-journal-entry window (a concurrent
    # `record_ideal` reading the same journal and both appending) and keeps table+witness
    # consistent (R5/R6). No `Store.add` runs inside, so the lock never nests.
    with locked_section(repo):
        itable = _load_ideal_table(repo)
        entry = None
        if journal and key in itable:
            # The journal is the unified operation log (U8/KTD6): this push is one `ideal_edit`
            # event carrying the prior ideal (+ witness). Tagged `kind` so `oplog.undo` dispatches
            # it. Kept inline (not `oplog.append`) because we already hold `locked_section` and
            # the flock is non-reentrant -- the same read-modify-write that closes the
            # double-journal-entry window (R5/R6). `applied` stays False until the table+witness
            # advance below lands: a crash in between leaves the entry unapplied, which `oplog.undo`
            # discards rather than executing as a phantom edit (0.2a/F6). `result` is the post-edit
            # op-set, so `undo` can tell work mined *after* this entry -- which its snapshot restore
            # would silently drop -- from the ops this edit itself produced (0.2c/F3).
            jtable = _load_ideal_journal(repo)
            prev_witness = _load_witnesses(repo).get(key)
            entry = {
                "kind": "ideal_edit",
                "ideal": sorted(itable[key]),
                "witness": prev_witness,
                "result": sorted(ideal.op_ids),
                "applied": False,
            }
            jtable.setdefault(key, []).append(entry)
            _save_ideal_journal(repo, jtable)
        # Translate this edit's delta into the exclusion OR-Set (1.1) -- the positive record the
        # provenance-derived ideal subtracts. An op this edit *removed* (revert/pin, or the undo of a
        # restore) is excluded with a fresh tag; an op it *re-included* (restore/cherry-pick, or the
        # undo of a revert) has its observed exclusion tags tombstoned so it re-enters. Reads the
        # pre-overwrite `itable[key]` as the prior ideal, so the first edit on an un-seeded ref (no
        # prior entry) records nothing -- it *is* the seed, and has removed nothing.
        before = set(itable.get(key, []))
        after = set(ideal.op_ids)
        removed, added = before - after, after - before
        # `record_exclusions=False` suppresses this translation for sync/land (Phase 1.2 §E): there,
        # the authoritative exclusion OR-Set is the per-key union `resolve` already computed from both
        # clones and `flush_reconciled_metadata` persisted. Re-deriving adds/tombstones from the merged
        # ideal's delta here would mint *fresh* tags for the same removals, so the two sides' OR-Sets
        # would never converge (each carries its own tag for the same reverted op). Local edit verbs
        # (revert/restore/undo) keep it on -- they *are* the source of the exclusion record.
        if record_exclusions and (removed or added):
            extable = load_exclusions(repo)
            orset = extable.get(key, ExclusionORSet())
            new_adds = frozenset((oid, uuid.uuid4().hex) for oid in removed)
            tomb = frozenset(tag for (oid, tag) in orset.adds if oid in added)
            extable[key] = ExclusionORSet(orset.adds | new_adds, orset.tombstones | tomb)
            save_exclusions(repo, extable)
        itable[key] = sorted(ideal.op_ids)
        _save_ideal_table(repo, itable)
        wtable = _load_witnesses(repo)
        wtable[key] = witness_sha
        _save_witnesses(repo, wtable)
        if entry is not None:
            entry["applied"] = True  # the edit landed -- the entry is now trustworthy for undo
            _save_ideal_journal(repo, jtable)


@dataclass(frozen=True)
class UndoResult:
    """What an ideal-edit undo restored: the prior `ideal`, the fresh `witness_sha` that
    re-materialized it, and the op-set delta versus the state undone (for the verb's report)."""

    ideal: Ideal
    witness_sha: str
    removed: frozenset[str]
    added: frozenset[str]


def _apply_ideal_edit_inverse(repo: str | Path, event: dict) -> UndoResult:
    """Restore the prior ideal an `ideal_edit` event carries (U8/KTD6): re-materialize it as a
    *fresh* witness commit and re-record it without journaling. History is an append-only op DAG,
    so undo is a forward edit re-establishing prior content, never a ref rewind; the stored
    `witness` is provenance only (the restore re-`put`s and uses the new sha). This is the ideal-edit
    restore that `oplog.apply_inverse` dispatches to for an `ideal_edit` event."""
    repo = Path(repo)
    all_ops = Store(repo).all_ops()
    current = current_ideal(repo)
    prev = Ideal.from_ops(frozenset(event["ideal"]), all_ops)
    sha = put(repo, prev, message="sgt undo: restore prior ideal")
    record_ideal(repo, prev, sha, journal=False)
    return UndoResult(prev, sha, removed=current.op_ids - prev.op_ids, added=prev.op_ids - current.op_ids)


def _dirty_conflicts(repo: Path, gb: GitBinding, materialized: dict[str, bytes]) -> set[str]:
    """Paths where `put()` would clobber an uncommitted change. A path is *dirty* when its
    on-disk bytes differ from HEAD's; it *conflicts* only when the ideal would additionally
    materialize different bytes there (or delete it). A dirty path the ideal already reproduces
    exactly -- the normal case once `get()` has folded the edit into the ideal -- is not a
    conflict, so the intended get()->put() flow never trips this guard. Only paths `put()` would
    actually write or delete are considered (`materialized` keys plus git-tracked paths); an
    untracked file the ideal doesn't cover is left untouched by `_write_working_tree`, so it can't
    conflict. `.sgt/` is skipped -- it's sgt's own state, not codebase content `put()` owns."""
    head = gb.head()
    tracked = set(_tracked_paths(repo))

    conflicts: set[str] = set()
    for path in set(materialized) | tracked:
        if path.startswith(".sgt/") or _writes_through_symlink(repo, path):
            continue  # symlinks are unmanaged (R3) -- never read/written through here either
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


def _tracked_paths(repo: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True
    )
    return [line for line in proc.stdout.splitlines() if line]


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


def _reproducible_content(repo: Path, all_ops: list | None = None) -> dict[str, bytes]:
    """Every path `code()` can produce from the store's *maximal valid ideal* -- all stored ops
    reduced to a grounded, fork-free set. A path present here is recoverable, so deleting its live
    bytes is safe; a path whose current bytes are absent (e.g. a dropped add/delete/re-add fork
    tip) is not (R4). Without a caller-supplied `all_ops`, reads frontier-selectively: reduce the
    maximal ideal over the footprint-only index, then load images for just its frontier producers
    -- the only images `code` opens -- instead of `Store.all_ops()`'s every-op decode."""
    if all_ops is None:
        index = opindex.index_ops(repo)
        maximal = Ideal.from_ops(order.reduce_to_ideal({op.id for op in index}, index), index)
        ops = ops_with_frontier_images(repo, maximal)
        return code(maximal, ops)
    maximal = order.reduce_to_ideal({op.id for op in all_ops}, all_ops)
    return code(Ideal.from_ops(maximal, all_ops), all_ops)


def materialization_skips(
    repo: str | Path, materialized: dict[str, bytes], all_ops: list | None = None
) -> dict[str, list[str]]:
    """What `_write_working_tree` would refuse to touch, computed *without* writing -- for `status`
    to surface (R3/R4). `unmanaged`: tracked symlink paths. `backstop_kept`: tracked paths the
    current ideal dropped whose live bytes no valid ideal over the store can regenerate."""
    repo = Path(repo)
    tracked = _tracked_paths(repo)
    unmanaged = [p for p in tracked if _writes_through_symlink(repo, p)]
    to_delete = [
        p for p in tracked
        if p not in materialized and not p.startswith(".sgt/")
        and (repo / p).is_file() and not _writes_through_symlink(repo, p)
    ]
    reproducible = _reproducible_content(repo, all_ops) if to_delete else {}
    backstop_kept = [p for p in to_delete if (repo / p).read_bytes() != reproducible.get(p)]
    return {"unmanaged": sorted(set(unmanaged)), "backstop_kept": sorted(backstop_kept)}


_TREE_CLASSES = ("drift", "unmanaged", "backstop_kept", "staged", "unseeded")


def fsck_tree(repo: str | Path) -> dict[str, list[str]]:
    """`sgt fsck --tree` (R2): compare `code(current_ideal)` against the HEAD tree and classify
    every divergent path. Only `drift` is a real finding -- bytes at HEAD that sgt never absorbed
    (`get` to absorb, or `put` to enforce the ideal, with opposite data-loss profiles). The rest
    are planned divergence: `unmanaged` (a symlink), `backstop_kept` (a path the ideal dropped
    whose HEAD bytes no valid ideal can regenerate), `staged` (an in-progress rewrite candidate),
    or `unseeded` (a ref this lens never tracked -- a fresh clone or detached HEAD, not drift)."""
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
    staged_active = state.load_json(repo, "staged", default=None) is not None

    candidates = set(materialized) | set(_tracked_paths(repo))
    reproducible: dict[str, bytes] | None = None
    for path in sorted(candidates):
        if path.startswith(".sgt/"):
            continue
        mat = materialized.get(path)
        head_bytes = gb.blob_bytes(head, path)
        # A live stage (U6) deliberately leaves an uncommitted rewrite candidate on the working
        # tree; a path whose *disk* bytes differ from the committed ideal is that candidate --
        # planned divergence classified `staged`, never `drift`. Checked before the HEAD comparison
        # because a stage's committed ideal still equals HEAD, so the mat-vs-HEAD test below can
        # never see the candidate (it lives only on disk).
        if staged_active and not _writes_through_symlink(repo, path):
            full = repo / path
            on_disk = full.read_bytes() if full.is_file() else None
            if on_disk != mat:
                result["staged"].append(path)
                continue
        if mat == head_bytes:
            continue  # the ideal reproduces HEAD's bytes exactly -- no divergence
        if _writes_through_symlink(repo, path):
            result["unmanaged"].append(path)
        elif not seeded:
            result["unseeded"].append(path)
        elif staged_active:
            result["staged"].append(path)
        elif mat is None and head_bytes is not None:
            if reproducible is None:
                # `None`, not `all_ops`: the reproducibility read folds the *maximal* ideal,
                # whose frontier reaches ops our frontier-selective list carries imageless.
                reproducible = _reproducible_content(repo, None)
            (result["backstop_kept"] if head_bytes != reproducible.get(path)
             else result["drift"]).append(path)
        else:
            result["drift"].append(path)
    return result


def _write_working_tree(
    repo: Path, materialized: dict[str, bytes], all_ops: list | None = None
) -> dict[str, list[str]]:
    """Write every materialized path; delete any git-tracked path the ideal no longer covers --
    the fold is total, so an absent path means the ideal genuinely doesn't include it. Two safety
    guards keep the fold non-destructive (R3/R4): never write or delete *through* a symlink (leaf
    or ancestor), and never delete a tracked path whose live bytes no valid ideal can regenerate
    (the backstop -- an add/delete/re-add fork drops a path from `code(I)` though its content is
    genuinely live, and a silent delete there is unrecoverable). Returns the skipped paths
    (`unmanaged`, `backstop_kept`) so the caller can surface them instead of losing them silently."""
    tracked = _tracked_paths(repo)

    unmanaged: list[str] = []
    for path, data in materialized.items():
        if _writes_through_symlink(repo, path):
            unmanaged.append(path)
            continue
        full = repo / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)

    to_delete = [
        p for p in tracked
        if p not in materialized and not p.startswith(".sgt/") and (repo / p).is_file()
    ]
    backstop_kept: list[str] = []
    reproducible: dict[str, bytes] | None = None
    for path in to_delete:
        if _writes_through_symlink(repo, path):
            unmanaged.append(path)
            continue
        if reproducible is None:
            reproducible = _reproducible_content(repo, all_ops)
        full = repo / path
        if full.read_bytes() != reproducible.get(path):
            backstop_kept.append(path)  # live bytes no valid ideal can regenerate -- keep (R4)
            continue
        full.unlink()

    return {"unmanaged": sorted(set(unmanaged)), "backstop_kept": sorted(backstop_kept)}
