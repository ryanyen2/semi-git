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


_DECLARED_FILE = "declared.json"

_CHUNK_BUDGET_SECONDS = 10.0  # KTD-3: one _sync() chunk's wall-clock ceiling on mine() work.


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


def _legacy_tag(a: str, b: str) -> str:
    """A deterministic tag for a pre-U21 flat G-Set edge lifted into the OR-Set. Deterministic (not
    a fresh UUID) so the lift is idempotent and two clones lifting the *same* legacy edge produce
    the same tag -- their union dedups it instead of double-counting, and a retraction of it
    propagates (as a shared legacy edge should)."""
    return f"legacy:{a}:{b}"


def _load_declared_flat(repo: Path) -> frozenset[Edge]:
    """The legacy flat G-Set at `.sgt/declared.json` (pre-U21), with the one-shot pre-U15
    local-path migration. Read only to *lift* into the OR-Set when no OR-Set exists yet, and to
    dual-write for old readers (D3); the OR-Set at `.sgt/declared_edges.json` is authoritative."""
    body = state.load_json(repo, "declared")
    if body is None:
        old_path = repo / ".sgt" / "local" / _DECLARED_FILE
        if not old_path.is_file():
            return frozenset()
        edges = frozenset(tuple(pair) for pair in json.loads(old_path.read_text(encoding="utf-8")))
        _save_declared(repo, edges)
        old_path.unlink()
        return edges
    return frozenset(tuple(pair) for pair in body)


def _save_declared(repo: Path, edges: frozenset[Edge]) -> None:
    """Write the flat G-Set at the legacy path (v0 shape). Retained as the old-reader dual-write
    target of `save_declared_orset` (D3) and for the state round-trip tests."""
    state.save_json(repo, "declared", sorted([a, b] for a, b in edges))


def _orset_from_body(body: dict | None, repo_or_flat) -> DeclaredORSet:
    if body is not None:
        return DeclaredORSet(
            adds=frozenset((a, b, tag) for a, b, tag in body.get("adds", [])),
            tombstones=frozenset(body.get("tombstones", [])),
        )
    # No OR-Set present: lift the legacy flat G-Set (add-only, deterministically tagged).
    flat = repo_or_flat if isinstance(repo_or_flat, frozenset) else _load_declared_flat(repo_or_flat)
    return DeclaredORSet(adds=frozenset((a, b, _legacy_tag(a, b)) for a, b in flat))


def load_declared_orset(repo: Path) -> DeclaredORSet:
    """The declared-edge OR-Set from the working tree, lifting the legacy flat G-Set when the OR-Set
    file doesn't exist yet (an un-migrated repo)."""
    return _orset_from_body(state.load_json(repo, "declared_orset"), Path(repo))


def declared_orset_at(gb: GitBinding, sha: str) -> DeclaredORSet:
    """A teammate's declared-edge OR-Set as committed at `sha` -- the historical-blob read `sync`
    unions by tag. Falls back to their legacy flat `declared.json` blob (an older sgt that never
    wrote an OR-Set), lifted the same way, so a mixed-version team still reconciles edges."""
    body = state.load_blob_json(gb, sha, "declared_orset")
    if body is not None:
        return _orset_from_body(body, frozenset())
    legacy = state.load_blob_json(gb, sha, "declared")
    flat = frozenset() if legacy is None else frozenset(tuple(pair) for pair in legacy)
    return _orset_from_body(None, flat)


def save_declared_orset(repo: Path, orset: DeclaredORSet) -> None:
    """Persist the OR-Set, and dual-write its live edges to the legacy flat path in v0 shape so an
    older sgt reader (D3 old-reader policy) still sees the current declared edges."""
    state.save_json(repo, "declared_orset", {
        "adds": sorted([a, b, tag] for a, b, tag in orset.adds),
        "tombstones": sorted(orset.tombstones),
    })
    _save_declared(repo, orset.live())


def declare_after(repo: Path, a: str, b: str) -> None:
    """`sgt after a b`: add the edge `a <= b` with a fresh, globally-unique tag (OR-Set add)."""
    from sgt.core import oplog

    snap = oplog.snapshot(repo, ["declared_orset", "declared"])  # inverse: the OR-Set before the add
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
        return Ideal.from_ops(frozenset(), [])  # nothing committed yet

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
            _ensure_fidelity(repo, gb, key, cached_ids, opindex.index_ops(repo))
            return Ideal.from_ops(cached_ids, opindex.index_ops(repo))

    # The dirty pass mines a virtual pending commit -- a full working-tree snapshot + whole-tree
    # entity graph -- so it costs O(files) even when nothing changed. Skip it unless some non-
    # `.sgt/` path actually differs from HEAD (R16); on a tree whose only churn is `.sgt/` state
    # it would rebuild the whole graph only to produce no source ops. `mine()` additionally skips
    # it whenever a chunk's own deadline cuts the history loop short (U1), so passing the caller's
    # true intent here -- even on a backward chunk -- is safe: it only actually runs once some
    # chunk finishes its historical work inside budget.
    include_dirty = gb.has_dirty_source()

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
        if opindex.is_stale(repo):
            opindex.rebuild(repo, store)
        else:
            opindex.apply_delta(repo, stored_ops)

    # Seed the persisted ideal from a provenance scan the first time this ref is tracked;
    # thereafter the stored set is authoritative (it can encode explicit exclusions -- U8's
    # revert/pin -- that a scan of git ancestry can't).
    ideal_table = _load_ideal_table(repo)
    already_seeded = key in ideal_table
    base_ids = set(ideal_table[key]) if already_seeded else _committed_ids_by_provenance(gb, store)

    # The durable ideal gains only newly-committed ops; the dirty overlay is never persisted, so a
    # discarded working-tree edit (e.g. `git checkout -- .`) simply stops appearing on the next
    # `get()` rather than lingering in the table. Reduce to a valid ideal *before* persisting
    # (U22.5): real history mined cold contains add/delete/re-add forks and predecessors squashed
    # out of this ref, so the raw union is not directly constructible -- persisting it unreduced
    # would leave an invalid `.sgt/local/ideal.json` on disk and then raise, corrupting the table.
    all_ops = opindex.index_ops(repo)
    committed_ids = set(order.reduce_to_ideal(base_ids | new_committed_ids, all_ops))

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
    return {"complete": complete, "reached_genesis": reached_genesis}


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
            f"put() would overwrite uncommitted changes: {sorted(conflicts)}"
        )
    _write_working_tree(repo, materialized, all_ops)
    # Committed in-tree recovery record of *this* commit's ideal (C5): written before the commit
    # so the blob at the witness SHA describes that SHA's own ideal, recoverable after a
    # squash/rebase strips the trailers below. The local per-ref table stays authoritative for the
    # current ref; this is the historical record `sync` reads from a teammate's arbitrary SHA.
    state.save_json(repo, "ideal", sorted(ideal.op_ids))
    return gb.commit_all(message, trailers=format_op_trailers(sorted(ideal.op_ids)))


def commit_materialized(repo: str | Path, ideal: Ideal, message: str) -> str:
    """Commit an `ideal` whose `code(I)` bytes are *already* on the working tree -- the rewrite
    staging path (U6). Unlike `put`, this neither re-mines the deliberately-dirty staged tree nor
    re-materializes: the staged bytes `stage` wrote are authoritative, so it only records the
    in-tree ideal recovery blob (C5) and commits with the op trailers. The caller (`rewrite.land`)
    owns the staleness check that guarantees the tree still equals the staged candidate, so the
    commit can never capture a mixture."""
    repo = Path(repo)
    gb = GitBinding(repo)
    state.save_json(repo, "ideal", sorted(ideal.op_ids))
    return gb.commit_all(message, trailers=format_op_trailers(sorted(ideal.op_ids)))


def record_ideal(
    repo: str | Path, ideal: Ideal, witness_sha: str, *, journal: bool = True, ref_key: str | None = None
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
        if journal and key in itable:
            # The journal is the unified operation log (U8/KTD6): this push is one `ideal_edit`
            # event carrying the prior ideal (+ witness). Tagged `kind` so `oplog.undo` dispatches
            # it; a legacy entry with no `kind` is read as `ideal_edit` too, so old journals still
            # undo. Kept inline (not `oplog.append`) because we already hold `locked_section` and
            # the flock is non-reentrant -- the same read-modify-write that closes the
            # double-journal-entry window (R5/R6).
            jtable = _load_ideal_journal(repo)
            prev_witness = _load_witnesses(repo).get(key)
            jtable.setdefault(key, []).append(
                {"kind": "ideal_edit", "ideal": sorted(itable[key]), "witness": prev_witness}
            )
            _save_ideal_journal(repo, jtable)
        itable[key] = sorted(ideal.op_ids)
        _save_ideal_table(repo, itable)
        wtable = _load_witnesses(repo)
        wtable[key] = witness_sha
        _save_witnesses(repo, wtable)


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
    tip) is not (R4)."""
    ops = all_ops if all_ops is not None else Store(repo).all_ops()
    maximal = order.reduce_to_ideal({op.id for op in ops}, ops)
    return code(Ideal.from_ops(maximal, ops), ops)


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

    all_ops = Store(repo).all_ops()
    materialized = code(current_ideal(repo), all_ops)
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
                reproducible = _reproducible_content(repo, all_ops)
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
