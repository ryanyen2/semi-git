"""Phase 1.2: `refs/ugh/state`, the git ref that carries ugh's committed metadata *off* the branch
tree.

Today ugh's authoritative state -- the `.sgt/ops/<id>` op store and a set of JSON tables -- lives
as tracked working-tree files under `.sgt/`. That is the root of two audited findings: every `save`
leaves those tables dirty so the next verb refuses (F1), and they collide on `git merge`/`rebase`
producing metadata conflicts a user never should see (F10). This module owns a dedicated repo-global
ref, `refs/ugh/state`, fetched and pushed alongside branches, into which that state moves. The
working-tree `.sgt/` files stay on disk exactly as before -- `Store.all_ops()` is far too hot a path
to read a git tree per call -- but become gitignored: the ref is a **transport/publication layer**,
materialized *into* the local mirror on fetch and rebuilt *from* it at each verb's transaction
boundary, never read per op.

The ref is a parent-chained series of commits advanced by `commit_tree` + `update_ref_cas`. It
carries correctness-bearing op blobs, so `publish_from_local` **raises** on final CAS failure rather
than swallowing it -- a branch commit's `Sgt-Op:` trailers name ops that live only here, so the ref must
be durable before the branch references it (the push-ordering invariant, wired in Phase 1.2 Step 6).

Scope of what travels here vs. stays in the branch tree is `_TRAVELING_TABLES` + the op store,
deliberately excluding the team-editable config (`oracle.json`) and the CORRECTNESS-CRITICAL `tiers.json`/`.sgtignore`
that `tiers.load_tiers_at` reads as-of each mined commit (LAW-0 -- moving them breaks mining
reproducibility). Everything under `.sgt/local/` stays local and gitignored as before.
"""

from __future__ import annotations

from pathlib import Path

from ugh import state
from ugh.core.store import Store, _write_atomic
from ugh.store.gitbind import GitBinding

STATE_REF = "refs/ugh/state"

# A scratch ref the sync fetch force-updates to the *remote's* state tip, so a fetch can hand
# `ingest` theirs' state sha without touching our authoritative local `STATE_REF` (keeping the
# dry-run `plan_sync` trace-free and our own publish history intact).
FETCH_STATE_REF = "refs/ugh/remote-state"

_PUBLISH_RETRIES = 5


class StateRefError(Exception):
    """A correctness-bearing state-ref advance could not be made durable (final CAS failure)."""


# The JSON tables that move off the branch tree onto `refs/ugh/state`. This is every committed
# artifact in `state._ARTIFACTS` EXCEPT the two that must stay tracked in the branch tree:
# `oracle_config` (genuinely team-editable config) and `tiers`
# (correctness-critical -- read as-of each mined commit, LAW-0). `exclusions` is promoted from
# local to shared here (Phase 1.2 Step 7): it travels on the ref and merges per-ref-key as an
# OR-Set in `resolve`, so a revert survives a teammate's sync and a fresh clone's cold bootstrap
# (F20). `ideal` still travels here during the transition; Step 4 stops writing it (the in-tree
# C5 recovery record is deleted) and it then simply stops being on disk to carry.
_TRAVELING_TABLES: tuple[str, ...] = (
    "pins",
    "tree",
    "ideal",
    "forks",
    "authored_features",
    "exclusions",
    "intent_prompts",
    "intent_themes",
    "intent_segments",
    "intent_segment_pins",
)

# The op store is content-addressed: a shared path holds byte-identical content on both sides by
# construction, so its union is a plain path-set union with no conflict to resolve.
def _is_content_addressed(rel_path: str) -> bool:
    return rel_path.startswith(".sgt/ops/")


# -- ref reads --------------------------------------------------------------------------------------

def read_sha(gb: GitBinding) -> str | None:
    """The `refs/ugh/state` tip sha, or None on a fresh clone / pre-1.2 repo where the ref is absent.
    A None here is the signal for the bootstrap fallback (mine from branch history, Step 6)."""
    return gb.rev_parse(STATE_REF)


def read_tree(gb: GitBinding, sha: str | None = None) -> dict[str, bytes]:
    """Every `.sgt/**` blob carried at `sha` (the state-ref tip when omitted), path -> raw bytes.
    Empty when the ref is absent."""
    tip = sha if sha is not None else read_sha(gb)
    if tip is None:
        return {}
    return gb.read_tree_blobs(tip)


# -- local mirror <-> ref ---------------------------------------------------------------------------

def _local_blobs(repo: Path) -> dict[str, bytes]:
    """The `{repo-relative-path: bytes}` snapshot of the traveling state as it sits in this clone's
    on-disk `.sgt/` mirror: every op file and the traveling JSON tables that exist. This is what `publish_from_local` serializes into the ref
    tree. Reads bytes verbatim so a blob round-trips byte-for-byte (op files are content-addressed;
    a re-encode could change their address)."""
    store = Store(repo)
    blobs: dict[str, bytes] = {}

    if store.ops_dir.is_dir():
        for p in store.ops_dir.iterdir():
            if p.is_file():
                blobs[f".sgt/ops/{p.name}"] = p.read_bytes()

    for name in _TRAVELING_TABLES:
        p = state.path(repo, name)
        if p.is_file():
            blobs[state.rel(name)] = p.read_bytes()

    return blobs


def publish_from_local(gb: GitBinding, repo: Path) -> str | None:
    """Rebuild the state-ref tree from this clone's local mirror and advance `refs/ugh/state` to it,
    returning the new tip sha (or the existing tip when nothing changed). Called once per mutating
    verb at its transaction boundary (inside the existing `locked_section`, so there is no concurrent
    *local* writer -- the CAS retry only guards a pathological interleave). Raises `StateRefError` on
    final CAS failure: this ref is correctness-bearing and a lost write must not pass silently."""
    blobs = _local_blobs(repo)
    for _ in range(_PUBLISH_RETRIES):
        tip = gb.rev_parse(STATE_REF)
        new_tree = gb.write_tree_from_blobs(blobs)
        if tip is not None and gb.rev_parse(f"{tip}^{{tree}}") == new_tree:
            return tip  # nothing changed since the last publish -- don't churn the ref
        parents = [tip] if tip else []
        commit = gb.commit_tree(new_tree, parents, "ugh state")
        if gb.update_ref_cas(STATE_REF, commit, tip):
            return commit
    raise StateRefError(f"could not advance {STATE_REF} after {_PUBLISH_RETRIES} attempts")


# -- transport: publish + push with the ordering invariant (§D, Step 6) ----------------------------

def _reconcile_remote_into_ref(gb: GitBinding, repo: Path, local_tip: str | None, remote_sha: str) -> bool:
    """Fold the remote state tip into our local ref as a CRDT 2-parent merge, so the next push is a
    fast-forward. The content-addressed op blobs union conflict-free (a shared
    path is byte-identical -- it *is* its content address); the mutable derived tables prefer *ours*
    (the just-synced, already-merged local copy is the more-reconciled side) and we fold in any table
    only the remote carries. Any content-addressed blob only the remote had is also written onto the
    local `.sgt/` mirror so it stays a faithful superset of the ref. Returns whether the CAS advanced
    our local `STATE_REF` (False = a concurrent local publisher moved it; the caller re-loops).

    This is deliberately not a field-level table merge: the derived tables self-heal on the next
    sync/mine, and the one table that genuinely needs a field-level CRDT -- the shared exclusion
    OR-Set -- lands in Step 7."""
    ours = _local_blobs(repo)
    theirs = read_tree(gb, remote_sha)

    merged = dict(ours)
    for rel_path, raw in theirs.items():
        merged.setdefault(rel_path, raw)  # ours wins on any shared path; fold in theirs-only paths
        if _is_content_addressed(rel_path) and rel_path not in ours:
            _write_atomic(repo / rel_path, raw)  # land theirs-only ops onto the local mirror

    new_tree = gb.write_tree_from_blobs(merged)
    parents = [p for p in (local_tip, remote_sha) if p is not None]
    commit = gb.commit_tree(new_tree, parents, "ugh state (merge)")
    return gb.update_ref_cas(STATE_REF, commit, local_tip)


def publish_and_push(gb: GitBinding, repo: Path, remote: str, retries: int = _PUBLISH_RETRIES) -> str:
    """Publish this clone's local mirror onto `refs/ugh/state` and push it to `remote`, reconciling
    on contention -- the correctness-bearing half of the Phase 1.2 push-ordering invariant (§D).

    A branch commit's `Sgt-Op:` trailers name ops that live only on this ref, so the ref must be
    durable on the remote *before* the branch references it. The caller therefore runs this FIRST and
    pushes the branch only if it returns; if it raises `StateRefError`, the caller must ABORT the
    branch push (never publish a branch whose ops aren't durable).

    On a non-fast-forward rejection (a teammate advanced the remote ref since our last fetch), fetch
    the remote tip, fold it into our local ref as a CRDT 2-parent merge (`_reconcile_remote_into_ref`),
    and re-push -- the merge descends from the remote tip, so the retry is a fast-forward. The state
    ref is a CRDT, so every retry auto-merges with no human conflict. Returns the pushed state sha;
    raises `StateRefError` if it cannot succeed within `retries`."""
    publish_from_local(gb, repo)  # local durable first (raises StateRefError on its own CAS loss)
    for _ in range(retries):
        if gb.push_ref(remote, f"{STATE_REF}:{STATE_REF}"):
            tip = gb.rev_parse(STATE_REF)
            if tip is None:
                raise StateRefError(f"pushed {STATE_REF} to {remote} but the local ref is unresolved")
            return tip
        # Push failed: either the remote moved (non-ff) or a transient error. Fetch the remote tip
        # and reconcile; a fetch that reveals we already contain it means the failure was transient.
        if not gb.fetch_ref(remote, f"+{STATE_REF}:{FETCH_STATE_REF}"):
            continue  # couldn't even fetch the remote tip -- transient; retry the push
        remote_sha = gb.rev_parse(FETCH_STATE_REF)
        local_tip = gb.rev_parse(STATE_REF)
        if remote_sha is None or remote_sha == local_tip or (
            local_tip is not None and gb.is_ancestor(remote_sha, local_tip)
        ):
            continue  # our tip already contains the remote -- retry the push (transient failure)
        _reconcile_remote_into_ref(gb, repo, local_tip, remote_sha)
    raise StateRefError(f"could not publish {STATE_REF} to {remote} after {retries} attempts")
