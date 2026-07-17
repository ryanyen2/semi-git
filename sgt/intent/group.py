"""The intent overlay's deterministic base (plan U1/U2): a total, LLM-free partition of the op
store by *why* each op happened, plus the dependency-graph-backed "across features" tiering.

This is rung 0/1 of the overlay's fallback ladder. It is a pure function of the already-mined
store + git history + the built feature tree -- no network, no LLM, byte-stable across rebuilds:

  - rung 0 (`atoms`): every op lands in exactly one `IntentAtom`, keyed on its *earliest*
    witnessing commit (the same earliest-provenance-in-history rule `history_view` uses for its
    commit-index axis). A commit is the smallest recorded intent; its subject is the atom's
    human label already, so the overlay is useful with zero further work.
  - rung 1 (`scope_bundles`): atoms sharing a conventional-commit scope (`fix(auth):` across
    three commits) coalesce into one bundle, reusing `cluster.commit_scope` -- the density plain
    per-commit grouping lacks on a young repo. Scope-less atoms stay singleton bundles here; the
    LLM rung (U4) may later reassign them.

The "across features" claim is never asserted -- it is computed (`feature_span`) and *tiered*
(`tier`) by how strongly a group's ops are connected in the op-DAG, so a reader knows which
groupings the dependency graph actually backs (`coupled`) versus which are only co-temporal
(`co-changed`) or purely thematic (`thematic`). See [[unified-direction-fallback-ladder]].
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sgt.core.op import Op
from sgt.core.store import Store
from sgt.lens.cluster import commit_scope
from sgt.store.gitbind import GitBinding

UNWITNESSED = "(unwitnessed)"  # the synthetic atom key for an op none of whose provenance
# commits appear in `history()` (mined from a detached/since-rewritten commit). Such an op is
# bucketed here rather than dropped, so `atoms` stays a *total* partition of the store (KTD2).


@dataclass(frozen=True)
class IntentAtom:
    """Every op sharing one earliest-witnessing commit -- the smallest recorded intent (KTD2).
    Frozen and hashable, like every value in the kernel it reads from. `commit_index` is the
    op's position in chronological `history()` (`-1` for the `UNWITNESSED` bucket, which sorts
    last); `subject` is the commit message's first line, the atom's ready-made human label."""

    commit_sha: str
    subject: str
    commit_index: int
    op_ids: frozenset[str]
    plan_ids: frozenset[str]
    session_ids: frozenset[str]
    scope: str | None  # `cluster.commit_scope(subject)`: the rung-1 coalescing key.


@dataclass(frozen=True)
class Bundle:
    """A rung-1 coalescing of atoms that declared the same conventional-commit scope. A scope-less
    atom is its own singleton bundle (`scope=None`) -- rung 2 (U4) may reassign it to a theme."""

    scope: str | None
    atoms: tuple[IntentAtom, ...]

    @property
    def op_ids(self) -> frozenset[str]:
        return frozenset().union(*(a.op_ids for a in self.atoms)) if self.atoms else frozenset()


def _atom_sort_key(atom: IntentAtom) -> tuple:
    """Chronological, `UNWITNESSED` last -- the stable order every projection off this module
    inherits (matching the `sgt.api` determinism discipline)."""
    return (atom.commit_index < 0, atom.commit_index, atom.commit_sha)


def atoms(repo: str | Path) -> list[IntentAtom]:
    """The deterministic rung-0 partition: group every stored op by the earliest of its
    provenance commits that appears in `GitBinding.history()` (the exact rule `history_view`
    uses), and build one `IntentAtom` per commit. Fully sorted (chronological, `UNWITNESSED`
    last). Called twice on an unchanged store it returns identical membership and ordering."""
    repo = Path(repo)
    rows = GitBinding(repo).history()
    commit_index = {sha: i for i, (sha, _parent, _subject) in enumerate(rows)}
    subject_of = {sha: subject for sha, _parent, subject in rows}
    ops = Store(repo).all_ops()

    buckets: dict[str, list[Op]] = {}
    for op in ops:
        witnessed = [sha for sha in op.provenance if sha in commit_index]
        key = min(witnessed, key=lambda s: commit_index[s]) if witnessed else UNWITNESSED
        buckets.setdefault(key, []).append(op)

    out: list[IntentAtom] = []
    for sha, group in buckets.items():
        subject = subject_of.get(sha, "")
        plan_ids = frozenset(a.plan for op in group for a in op.attribution if a.plan)
        session_ids = frozenset(a.session for op in group for a in op.attribution if a.session)
        out.append(IntentAtom(
            commit_sha=sha,
            subject=subject,
            commit_index=commit_index.get(sha, -1),
            op_ids=frozenset(op.id for op in group),
            plan_ids=plan_ids,
            session_ids=session_ids,
            scope=commit_scope(subject) if sha != UNWITNESSED else None,
        ))
    out.sort(key=_atom_sort_key)
    return out


def scope_bundles(atom_list: list[IntentAtom]) -> list[Bundle]:
    """Rung 1: coalesce atoms that share a conventional-commit scope into one bundle; every
    scope-less atom stays its own singleton. Deterministically ordered (by each bundle's earliest
    member, then scope). Reuses `cluster.commit_scope` (already stored on each atom as `.scope`)
    -- never a reimplementation of scope parsing."""
    by_scope: dict[str, list[IntentAtom]] = {}
    singletons: list[Bundle] = []
    for atom in atom_list:
        if atom.scope is None:
            singletons.append(Bundle(scope=None, atoms=(atom,)))
        else:
            by_scope.setdefault(atom.scope, []).append(atom)

    bundles = [
        Bundle(scope=scope, atoms=tuple(sorted(members, key=_atom_sort_key)))
        for scope, members in by_scope.items()
    ]
    bundles.extend(singletons)
    bundles.sort(key=lambda b: (_atom_sort_key(min(b.atoms, key=_atom_sort_key)), b.scope or ""))
    return bundles


# -- U2: cross-feature span + dependency tiering --------------------------------------------------

COUPLED = "coupled"       # a real requires/reference edge crosses a feature boundary within the
CO_CHANGED = "co-changed"  # group -- strong: the dependency graph itself backs the cross-feature
THEMATIC = "thematic"      # claim. co-changed: same commit, spans features, no such edge (medium).
# thematic: bundled across *different* commits by scope or LLM theme only, no dependency edge
# (weak) -- the dependency graph cannot back a purely thematic claim, and the tier says so (KTD3).


def feature_span(op_ids, op_leaf: dict[str, str]) -> set[str]:
    """The distinct features a group of ops touches, per the built tree's `op_leaf` vote. An op
    with no leaf (tree not built, or a hollow/off-chain op) is skipped rather than erroring --
    `feature_span` degrades gracefully to `set()` when there's no tree at all."""
    return {op_leaf[op_id] for op_id in op_ids if op_id in op_leaf}


def tier(
    group_op_ids: frozenset[str], commit_shas: frozenset[str], all_ops: list[Op],
    declared, op_leaf: dict[str, str],
) -> str:
    """`coupled | co-changed | thematic` for one group (KTD3). `commit_shas` is the set of
    distinct commits the group's atoms came from -- a single-commit group can only ever be
    `co-changed` (same-commit, cross-feature, no edge) or below `coupled`; a multi-commit group
    with no dependency edge is `thematic`. Reuses `order.downset_in` exactly as
    `proposal_review_view` does, restricted to the group's own op-set -- never a new closure walk."""
    from sgt.core import order

    span = feature_span(group_op_ids, op_leaf)
    if len(span) < 2:
        return CO_CHANGED if len(commit_shas) <= 1 else THEMATIC

    for op_id in group_op_ids:
        leaf = op_leaf.get(op_id)
        if leaf is None:
            continue
        closure = order.downset_in(op_id, group_op_ids, all_ops, declared)
        if any(op_leaf.get(oid) not in (None, leaf) for oid in closure):
            return COUPLED

    return CO_CHANGED if len(commit_shas) <= 1 else THEMATIC
