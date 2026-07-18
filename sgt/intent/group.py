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
    per-commit grouping lacks on a young repo -- but only when they are also structurally
    connected (`order.components_in`, plan U3): a scope string alone is a naming coincidence, not
    evidence the commits are one change. Two same-scope atoms with no chain/reference/declared
    edge between their ops become two separate bundles under the same scope, never one false
    merge. Scope-less atoms stay singleton bundles here; the LLM rung (U4) may later reassign them.

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


def scope_bundles(atom_list: list[IntentAtom], all_ops: list[Op], declared=frozenset()) -> list[Bundle]:
    """Rung 1: coalesce atoms that share a conventional-commit scope into one bundle -- but only
    the atoms within that scope that are also structurally connected (`order.components_in`,
    restricted to the scope's own atoms' op-ids). Two same-scope atoms with no chain/reference/
    declared edge between them (e.g. an unrelated CVE patch and a doc typo fix that coincidentally
    both declared `fix(auth):`) become two separate same-scope bundles, not one false merge --
    a same-scope atom that connects to no other same-scope atom is its own singleton bundle,
    same shape as a scope-less singleton. Two atoms merge whenever *any* op of one shares a
    component with *any* op of the other -- not a majority vote over each atom's own ops, which
    an atom's off-chain anchor/residue bookkeeping ops (structurally isolated by construction)
    would otherwise swamp with noise votes and split a genuinely connected pair. Every atom still
    lands in exactly one bundle (the total-partition property is preserved; only which bundle
    changes). Deterministically ordered (by each bundle's earliest member, then scope). Reuses
    `cluster.commit_scope` (already stored on each atom as `.scope`) -- never a reimplementation
    of scope parsing."""
    from sgt.core import order

    by_scope: dict[str, list[IntentAtom]] = {}
    singletons: list[Bundle] = []
    for atom in atom_list:
        if atom.scope is None:
            singletons.append(Bundle(scope=None, atoms=(atom,)))
        else:
            by_scope.setdefault(atom.scope, []).append(atom)

    bundles: list[Bundle] = []
    for scope, members in by_scope.items():
        scope_op_ids = frozenset().union(*(a.op_ids for a in members))
        op_component: dict[str, int] = {}
        for idx, component in enumerate(order.components_in(scope_op_ids, all_ops, declared)):
            for op_id in component:
                op_component[op_id] = idx

        # Union-find over atoms: two atoms merge iff they share at least one op-component.
        parent = list(range(len(members)))

        def find(i: int) -> int:
            while parent[i] != i:
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        component_atoms: dict[int, list[int]] = {}
        for atom_idx, atom in enumerate(members):
            for comp_idx in {op_component[oid] for oid in atom.op_ids}:
                component_atoms.setdefault(comp_idx, []).append(atom_idx)
        for atom_idxs in component_atoms.values():
            for other in atom_idxs[1:]:
                union(atom_idxs[0], other)

        groups: dict[int, list[IntentAtom]] = {}
        for atom_idx, atom in enumerate(members):
            groups.setdefault(find(atom_idx), []).append(atom)

        bundles.extend(
            Bundle(scope=scope, atoms=tuple(sorted(group_atoms, key=_atom_sort_key)))
            for group_atoms in groups.values()
        )

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
    with no dependency edge is `thematic`. Uses `order.components_in` -- undirected connectivity
    restricted to the group's own op-set -- rather than `downset_in`/`upset_in`: those assume
    `group_op_ids` is itself a valid, downward-closed ideal (they walk `_ordered_chains`, which
    raises `KeyError` on a modify op whose chain head is outside the set), and an arbitrary
    commit's or scope-bundle's op-set is not guaranteed to be one. `components_in` has no such
    precondition -- it answers "are these ops linked at all," which is exactly what a tier needs."""
    from sgt.core import order

    span = feature_span(group_op_ids, op_leaf)
    if len(span) < 2:
        return CO_CHANGED if len(commit_shas) <= 1 else THEMATIC

    for component in order.components_in(group_op_ids, all_ops, declared):
        leaves = {op_leaf[oid] for oid in component if oid in op_leaf}
        if len(leaves) >= 2:
            return COUPLED

    return CO_CHANGED if len(commit_shas) <= 1 else THEMATIC


# -- U8: resolve an intent-revert target + validate a --subset selection ---------------------------

def resolve_group(
    target: str, themes: dict[str, dict], all_atoms: list[IntentAtom],
) -> tuple[str, list[IntentAtom]] | None:
    """Resolve `sgt intent revert <target>` to `(kind, member_atoms)` -- `kind` is `"theme"` for
    an exact theme-id match (`themes`, the persisted `.sgt/intent/themes.json` body) or `"atom"`
    for a unique commit-sha prefix match against `all_atoms`; `None` if neither resolves. Mirrors
    `cli.intent._show`'s lookup so `show` and `revert` agree on what a target names."""
    entry = themes.get(target)
    if entry is not None:
        by_sha = {a.commit_sha: a for a in all_atoms}
        member = [by_sha[sha] for sha in entry["atom_shas"] if sha in by_sha]
        return "theme", member
    matches = [a for a in all_atoms if a.commit_sha.startswith(target)]
    if len(matches) == 1:
        return "atom", matches
    return None


def group_requires(
    member_atoms: list[IntentAtom], all_ops: list[Op], declared,
) -> dict[str, list[str]]:
    """Per member atom X, which *other* member atoms' ops would also be swept away by reverting
    X alone -- `order.upset_in` (everything that transitively builds on X, restricted to the
    group's own op-set), not `downset_in` (what X depends on): revert removes an op-set's up-set
    (`plan_revert_op_set`), so the atom whose *removal cascades into* another atom is the one that
    can't be selected without it, not the other way around (an atom X can always be reverted
    without whatever X itself structurally requires -- that dependency stays untouched). This is
    the U8 closure-validation input: selecting X for `--subset` while excluding an atom X's
    removal would also remove must be refused by name, not a silent extra deletion."""
    from sgt.core import order

    group_op_ids = frozenset().union(*(a.op_ids for a in member_atoms)) if member_atoms else frozenset()
    owner = {op_id: a.commit_sha for a in member_atoms for op_id in a.op_ids}

    requires: dict[str, list[str]] = {}
    for atom in member_atoms:
        closure: set[str] = set()
        for op_id in atom.op_ids:
            closure |= order.upset_in(op_id, group_op_ids, all_ops, declared)
        requires[atom.commit_sha] = sorted({
            owner[oid] for oid in closure if oid in owner and owner[oid] != atom.commit_sha
        })
    return requires


def apply_subset(
    member_atoms: list[IntentAtom], requires: dict[str, list[str]], subset: list[str] | None,
) -> tuple[list[IntentAtom], str | None]:
    """Resolve `--subset <commit-sha>...` against a group's member atoms and validate the
    closure `group_requires` computed: refuses (by name) selecting an atom whose own revert would
    also sweep away an atom excluded from the subset. `subset=None` means "the whole group" --
    returns `member_atoms` unchanged. Returns
    `(chosen_atoms, error_message)`; exactly one is meaningful per call."""
    if subset is None:
        return member_atoms, None
    chosen: list[IntentAtom] = []
    for prefix in subset:
        matches = [a for a in member_atoms if a.commit_sha.startswith(prefix)]
        if len(matches) != 1:
            return [], f"subset entry {prefix!r} does not match exactly one atom in this group"
        chosen.append(matches[0])
    chosen_shas = {a.commit_sha for a in chosen}
    for atom in chosen:
        missing = [sha for sha in requires.get(atom.commit_sha, ()) if sha not in chosen_shas]
        if missing:
            names = ", ".join(sha[:8] for sha in missing)
            return [], f"cannot revert {atom.commit_sha[:8]} without also reverting {names} (required by closure)"
    return chosen, None
