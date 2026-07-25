"""`sgt migrate ops-v3` (plan U10, R15): carry an existing **v2** op store across the v3 identity
boundary U9 opened. Every op's id embeds `miner_version` (`op.compute_id`), so the 2->3 bump re-keys
*every* op even when its content is unchanged -- a store left half-crossed is a mixed-version store,
which `fsck` flags (U2 backstop). This migration crosses the whole store, and every op-id-bearing
artifact with it, as one resumable write set.

It follows the U21 gated migration pattern at full-store scope:
dry-run default returns a report and writes nothing; `--apply` performs the atomic, idempotent
crossing under a manifest so a crash mid-apply resumes to the same final state rather than stranding
a mixed store.

Why re-mine rather than re-hash: a pure content re-hash of each v2 op (`compute_id(..., "3")`)
re-keys it but *keeps* the v2 pseudo-fork that dropped ~20% of closure (U22.5) -- both births of a
re-added file still claim `(sym, None)`. Re-mining under v3 is what applies rebirth chaining and
representation-flip bridging to the existing history, so the migrated store's current ideal
materializes the reborn files again. The re-hash is still used to *recognize* the unchanged ops when
building the old->new map; the rebirth/flip-affected ones (whose footprint changed) are matched by
their unchanged `(symbol, after_version)` frontier instead.

What orphans by design: published oracle claims (`.sgt/claims/`) are keyed by `ideal_key`, a hash
over an op-id *set*; a full re-key changes every such hash, so no claim survives -- they are counted
and reported, never re-keyed (a re-keyed claim would assert a verdict a runner never actually
produced on the v3 op-set). Pins are not op-id-bearing (`assign` values are feature ids, `must_link`/
`cannot_link` name symbols, which are miner-version-independent), so they are left untouched. The
per-clone `witness` table records commit shas, not op ids, and is likewise untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from sgt import state
from sgt.core import opindex, order
from sgt.core.mine import mine
from sgt.core.op import BOTTOM, MINER_VERSION, Op, compute_id, is_bottom
from sgt.core.store import Store, _serialize, _write_atomic, locked_section
from sgt.store.gitbind import GitBinding

_MANIFEST = "migration_manifest"


@dataclass(frozen=True)
class OpsV3Report:
    """The result of a dry-run or applied `ops-v3` migration. `total_ops` is how many pre-v3 ops the
    map covers; `rekey_clean` re-hash cleanly under v3 (unchanged content), `rebirth_remapped` are
    the rebirth/flip-affected ops matched by their `(symbol, after)` frontier, and `orphaned` are the
    v2 ids with no v3 counterpart (never silently dropped). `artifacts` names every op-id-bearing
    artifact whose bytes change; `dropped_refs` counts op-id references dropped as unmappable across
    all of them; `claims_orphaned` is the count of claim files a re-key strands by design."""

    dry_run: bool
    changed: bool
    total_ops: int
    rekey_clean: int
    rebirth_remapped: int
    orphaned: tuple[str, ...]
    artifacts: tuple[str, ...]
    dropped_refs: int
    claims_orphaned: int


@dataclass(frozen=True)
class _Plan:
    """The computed crossing: the canonical v3 op set (from re-mining), the old->new id map, the
    ops with no v3 counterpart, and the recovered current-ref ideal. Pure -- built without writing."""

    v3_ops: list[Op]
    mapping: dict[str, str]  # v2 id -> v3 id, only the mappable ones
    orphaned: list[str]
    rekey_clean: int
    rebirth_remapped: int
    current_key: str
    current_ideal: list[str]  # the recovered v3 ideal for the current ref (closure restored)

    @property
    def needs_migration(self) -> bool:
        return bool(self.mapping) or bool(self.orphaned)


# -- old->new id map ------------------------------------------------------------------------------


def _afterset(op: Op) -> frozenset[tuple[str, str]]:
    """An op's frontier as `{(symbol, after_version)}` -- the part rebirth/flip chaining leaves
    unchanged (only `before_version` moves from `None`/bare `⊥` to a salted bottom). Two mining runs
    across the v2/v3 boundary agree on it for the same content, so it is the join key for a remapped
    op. Every bottom (bare `⊥` under v2, salted `⊥@sha` under v3) is canonicalized to `⊥` so a v2
    prune whose `after` is the bare sentinel still matches its v3 counterpart's salted one."""
    return frozenset(
        (sym, BOTTOM if is_bottom(after) else after) for sym, (_before, after) in op.footprint.items()
    )


def _build_map(v2_ops: list[Op], v3_ops: list[Op]) -> tuple[dict[str, str], list[str], int, int]:
    """Map each pre-v3 op to its v3 counterpart. Two rungs, most precise first:

    1. **Re-hash** -- an op whose content is unchanged under v3 hashes (with `miner_version="3"`) to
       an id present in the re-mined set; that is a clean re-key, unambiguous by construction.
    2. **Frontier** -- a rebirth/flip-affected op's footprint changed (`before` now a salted bottom),
       so its re-hash is absent; it matches the single v3 op producing the same `(symbol, after)`
       set. An ambiguous frontier (an identical-content rebirth *cycle* mints two v3 ops with the
       same frontier but distinct salted bottoms) is not matched -- the op orphans rather than
       binding to the wrong tip.

    Returns `(mapping, orphaned, rekey_clean, rebirth_remapped)`."""
    v3_ids = {op.id for op in v3_ops}
    by_frontier: dict[frozenset[tuple[str, str]], str] = {}
    ambiguous: set[frozenset[tuple[str, str]]] = set()
    for op in v3_ops:
        key = _afterset(op)
        if key in by_frontier:
            ambiguous.add(key)
        else:
            by_frontier[key] = op.id

    mapping: dict[str, str] = {}
    orphaned: list[str] = []
    rekey_clean = 0
    rebirth_remapped = 0
    for op in v2_ops:
        rehash = compute_id(op.footprint, op.images, op.requires, op.kind, MINER_VERSION)
        if rehash in v3_ids:
            mapping[op.id] = rehash
            rekey_clean += 1
            continue
        key = _afterset(op)
        if key in by_frontier and key not in ambiguous:
            mapping[op.id] = by_frontier[key]
            rebirth_remapped += 1
            continue
        orphaned.append(op.id)
    return mapping, sorted(orphaned), rekey_clean, rebirth_remapped


def _plan(repo: Path) -> _Plan:
    """Build the crossing without writing. Re-mines the full history under v3 (recovering the
    rebirth/flip closure v2 lost), then maps the store's pre-v3 ops onto it and recovers the current
    ref's v3 ideal from provenance -- the same reduction `_committed_ids_by_provenance` uses, so the
    recovered ideal is grounded and fork-free (a valid ideal `fsck` accepts)."""
    gb = GitBinding(repo)
    store = Store(repo)
    v2_ops = [op for op in store.all_ops() if op.miner_version != MINER_VERSION]
    v3_ops, _last_sha = mine(repo)
    mapping, orphaned, clean, remapped = _build_map(v2_ops, v3_ops)

    from sgt.core.lens import _ref_key

    key = _ref_key(gb) or (gb.head() or "HEAD")
    ref_commits = set(gb.commit_shas())
    included = {op.id for op in v3_ops if set(op.provenance) & ref_commits}
    current = sorted(order.reduce_to_ideal(included, v3_ops))
    return _Plan(
        v3_ops=v3_ops, mapping=mapping, orphaned=orphaned, rekey_clean=clean,
        rebirth_remapped=remapped, current_key=key, current_ideal=current,
    )


# -- artifact remapping (pure: old body + map -> new body) ----------------------------------------


def _remap_ids(ids, mapping: dict[str, str]) -> list[str]:
    """Every id in `ids` that maps, in the mapped id's sorted order -- unmappable ids are dropped."""
    return sorted({mapping[i] for i in ids if i in mapping})


def _remap_ideal_table(old: dict, mapping, current_key, current_ideal, v3_ops) -> tuple[dict, int]:
    """The current ref's entry becomes the freshly-recovered v3 ideal (so the closure v2 dropped
    lands); every other entry is remapped and re-reduced to a valid ideal (`fsck` validates each).
    Returns `(new_table, dropped_ref_count)`."""
    new: dict[str, list[str]] = {}
    dropped = 0
    for key, ids in old.items():
        if key == current_key:
            new[key] = list(current_ideal)
            continue
        mapped = _remap_ids(ids, mapping)
        dropped += len(set(ids)) - len(mapped)
        new[key] = sorted(order.reduce_to_ideal(mapped, v3_ops))
    new[current_key] = list(current_ideal)  # ensure it exists even on a ref never tracked before
    return new, dropped


def _remap_journal(old: dict, mapping, v3_ops) -> tuple[dict, int]:
    """Each undo-stack entry's `ideal` is remapped and re-reduced (so a later `sgt undo` restores a
    valid ideal); its `witness` sha rides along unchanged."""
    new: dict[str, list[dict]] = {}
    dropped = 0
    for key, entries in old.items():
        out = []
        for e in entries:
            ids = e.get("ideal", [])
            mapped = _remap_ids(ids, mapping)
            dropped += len(set(ids)) - len(mapped)
            out.append({"ideal": sorted(order.reduce_to_ideal(mapped, v3_ops)), "witness": e.get("witness")})
        new[key] = out
    return new, dropped


def _remap_forks(old: list, mapping) -> tuple[list, int]:
    """Each open-fork record's two tips are remapped; a record with an unmappable tip is dropped (the
    op it named no longer exists -- and a v2 rebirth pseudo-fork is exactly what v3 no longer forks).
    The `sgt merge-op` remedy is regenerated from the new tip ids."""
    new: list[dict] = []
    dropped = 0
    for rec in old:
        tips = rec.get("tips", [])
        if len(tips) == 2 and tips[0] in mapping and tips[1] in mapping:
            a, b = mapping[tips[0]], mapping[tips[1]]
            new.append({"symbol": rec.get("symbol"), "tips": [a, b], "remedy": f"sgt merge-op {a[:8]} {b[:8]}"})
        else:
            dropped += 1
    return sorted(new, key=lambda r: (r["symbol"] or "", r["tips"])), dropped


def _remap_orset(body: dict, mapping) -> tuple[dict, int]:
    """The declared-edge OR-Set: each add's two endpoints are op ids -- remapped, keeping the add's
    tag (so the merge identity survives). An add with an unmappable endpoint is dropped. Tombstones
    are tags (opaque strings), carried through unchanged."""
    adds = []
    dropped = 0
    for a, b, tag in body.get("adds", []):
        if a in mapping and b in mapping:
            adds.append([mapping[a], mapping[b], tag])
        else:
            dropped += 1
    return {"adds": sorted(adds), "tombstones": sorted(body.get("tombstones", []))}, dropped


def _remap_staged(body: dict, mapping, v3_ops) -> tuple[dict, int]:
    """The staged rewrite candidate's `op_ids` are remapped and re-reduced to a valid ideal; `verb`/
    `target` ride along unchanged."""
    ids = body.get("op_ids", [])
    mapped = _remap_ids(ids, mapping)
    dropped = len(set(ids)) - len(mapped)
    out = dict(body)
    out["op_ids"] = sorted(order.reduce_to_ideal(mapped, v3_ops))
    return out, dropped


def _remap_drafts(old: dict, mapping, hollow_map) -> tuple[dict, int]:
    """Each registered draft's `hollow_ids` are remapped through the *hollow* map (hollows re-key
    separately, below), and its `meta.removed_ids`/`meta.required_ids` -- main-chain op ids -- through
    the main map. Unmappable ids are dropped. The draft's own id keys stay put (historical)."""
    new: dict[str, dict] = {}
    dropped = 0
    for did, rec in old.items():
        out = dict(rec)
        # hollow_ids re-key through the hollow map (a hollow op re-keys deterministically, so every
        # live one has a new id); an id with no mapped file is left as-is (historical).
        out["hollow_ids"] = [hollow_map.get(i, i) for i in rec.get("hollow_ids", [])]
        meta = dict(rec.get("meta", {}))
        for fld in ("removed_ids", "required_ids"):
            if fld in meta:
                before = meta[fld]
                meta[fld] = [mapping[i] for i in before if i in mapping]
                dropped += sum(1 for i in before if i not in mapping)
        out["meta"] = meta
        new[did] = out
    return new, dropped


# -- apply --------------------------------------------------------------------------------------


def _rekey_hollows(store: Store, mapping_out: dict[str, str]) -> None:
    """Hollow (off-chain plan-intake) ops are never mined from history, so they carry no v3
    counterpart to match -- they are re-keyed by pure re-hash under v3 (their `requires` name content
    versions, which are miner-version-independent). Records the old->new hollow map in `mapping_out`
    for the drafts rewrite, and rewrites each file at its new id, removing the old."""
    for op in store.all_hollow_ops():
        new_id = compute_id(op.footprint, op.images, op.requires, op.kind, MINER_VERSION)
        if new_id == op.id and op.miner_version == MINER_VERSION:
            continue
        new_op = replace(op, id=new_id, miner_version=MINER_VERSION)
        _write_atomic(store.hollow_dir / new_id, _serialize(new_op))
        if new_id != op.id:
            (store.hollow_dir / op.id).unlink(missing_ok=True)
        mapping_out[op.id] = new_id


def _rewrite_proposals(repo: Path, mapping) -> int:
    """Remap each proposal's `base_ideal_ids`/`delta_ids` (drop unmappable), keeping its filename and
    body id (historical -- an old proposal is a record, not re-content-addressed). This keeps
    `state.load_proposal`/`api.proposal_view` reading it without a crash on a pre-migration id."""
    touched = 0
    for name in state.list_proposal_files(repo):
        body = state.load_proposal(repo, name)
        if body is None:
            continue
        new = dict(body)
        new["base_ideal_ids"] = sorted(mapping[i] for i in body.get("base_ideal_ids", []) if i in mapping)
        new["delta_ids"] = sorted(mapping[i] for i in body.get("delta_ids", []) if i in mapping)
        if new != body:
            state.save_proposal(repo, name, new)
            touched += 1
    return touched


def _write_ops(store: Store, v3_ops: list[Op]) -> None:
    for op in v3_ops:
        store.add(op)  # content-addressed + append-only: a no-op if already present (idempotent)


def _prune_pre_v3(store: Store, v3_ids: set[str]) -> None:
    """Remove every committed op file that is not part of the v3 set -- the pre-v3 ids. After this
    the store is pure v3; before it (mid-apply) it is transiently mixed, which `fsck` flags and a
    resume completes."""
    if not store.ops_dir.is_dir():
        return
    for p in store.ops_dir.iterdir():
        if p.is_file() and p.name not in v3_ids:
            p.unlink()


def _full_map(v3_ops, mapping: dict[str, str]) -> dict[str, str]:
    """The remap map extended with a v3->itself identity for every v3 op. This makes every artifact
    rewrite idempotent: re-running it against an artifact a crashed apply already migrated to v3 ids
    leaves those ids in place rather than dropping them as 'unmappable' (the v2->v3 map alone knows
    only pre-v3 ids). The v2->v3 entries take precedence, so a still-pre-v3 artifact migrates fully."""
    full = {op.id: op.id for op in v3_ops}
    full.update(mapping)
    return full


def _execute(repo: Path, v3_ops, mapping, current_key, current_ideal) -> tuple[list[str], int]:
    """Perform the crossing (idempotent). Writes the v3 ops, rewrites every artifact under the map,
    then prunes the pre-v3 op files so the store ends pure v3. Returns `(changed_artifacts, dropped)`.
    Callers write the resume manifest *before* this and clear it after."""
    store = Store(repo)
    _write_ops(store, v3_ops)
    mapping = _full_map(v3_ops, mapping)  # idempotent under resume against a partially-migrated store

    changed: list[str] = []
    dropped = 0
    # Metadata rewrites under one lock (ops were added above, so `Store.add`'s lock never nests here).
    with locked_section(repo):
        # ideal table (current ref recovers closure; other refs remap+reduce).
        old_table = state.load_json(repo, "ideal_table", default={})
        new_table, d = _remap_ideal_table(old_table, mapping, current_key, current_ideal, v3_ops)
        dropped += d
        if new_table != old_table:
            state.save_json(repo, "ideal_table", new_table)
            changed.append("ideal_table")

        old_journal = state.load_json(repo, "ideal_journal", default={})
        if old_journal:
            new_journal, d = _remap_journal(old_journal, mapping, v3_ops)
            dropped += d
            if new_journal != old_journal:
                state.save_json(repo, "ideal_journal", new_journal)
                changed.append("ideal_journal")

        old_forks = state.load_json(repo, "forks", default=None)
        if old_forks:
            new_forks, d = _remap_forks(old_forks, mapping)
            dropped += d
            if new_forks != old_forks:
                state.save_json(repo, "forks", new_forks)
                changed.append("forks")

        old_orset = state.load_json(repo, "declared_orset", default=None)
        if old_orset:
            new_orset, d = _remap_orset(old_orset, mapping)
            dropped += d
            if new_orset != old_orset:
                from sgt.core.lens import DeclaredORSet, save_declared_orset

                save_declared_orset(repo, DeclaredORSet(
                    adds=frozenset((a, b, t) for a, b, t in new_orset["adds"]),
                    tombstones=frozenset(new_orset["tombstones"]),
                ))
                changed.append("declared_orset")

        old_staged = state.load_json(repo, "staged", default=None)
        if old_staged:
            new_staged, d = _remap_staged(old_staged, mapping, v3_ops)
            dropped += d
            if new_staged != old_staged:
                state.save_json(repo, "staged", new_staged)
                changed.append("staged")

        # hollows re-key first (their old->new map feeds the drafts rewrite).
        hollow_map: dict[str, str] = {}
        _rekey_hollows(store, hollow_map)
        if hollow_map:
            changed.append("hollows")

        old_drafts = state.load_json(repo, "drafts", default=None)
        if old_drafts:
            new_drafts, d = _remap_drafts(old_drafts, mapping, hollow_map)
            dropped += d
            if new_drafts != old_drafts:
                state.save_json(repo, "drafts", new_drafts)
                changed.append("drafts")

        if _rewrite_proposals(repo, mapping):
            changed.append("proposals")

        _prune_pre_v3(store, {op.id for op in v3_ops})

    # Every op just got a fresh id (miner_version bump) -- eagerly rebuild the opindex sidecar
    # (rather than leaving it to self-heal on the next read) so a read right after `--apply`
    # reflects the crossing immediately.
    opindex.rebuild(repo, store)
    return changed, dropped


def migrate_ops_v3(repo: str | Path, *, dry_run: bool = True) -> OpsV3Report:
    """Cross the store (and every op-id-bearing artifact) from miner v2 to v3. Dry-run (default)
    computes the plan and returns a report, writing nothing. `--apply` writes a resume manifest
    recording the old->new map and recovered ideal, performs the atomic crossing, then clears the
    manifest -- so a crash mid-apply is detected (the manifest survives, and the transient mixed
    store `fsck` flags) and a second `--apply` resumes to the same final state from the manifest's
    map rather than recomputing it against half-deleted op files. Idempotent: an already-v3 store
    with no manifest is a no-op."""
    repo = Path(repo)
    manifest = state.load_json(repo, _MANIFEST, default=None)

    if dry_run:
        plan = _plan(repo)
        changed = _changed_artifacts_preview(repo, plan)
        return OpsV3Report(
            dry_run=True, changed=False, total_ops=len(plan.mapping) + len(plan.orphaned),
            rekey_clean=plan.rekey_clean, rebirth_remapped=plan.rebirth_remapped,
            orphaned=tuple(plan.orphaned), artifacts=tuple(changed[0]), dropped_refs=changed[1],
            claims_orphaned=len(state.list_claim_files(repo)),
        )

    if manifest is not None:
        # Resume: v3 ops re-mine deterministically, but the map must come from the manifest -- the
        # pre-v3 op files it was built from may already be pruned.
        v3_ops, _last_sha = mine(repo)
        mapping = manifest["map"]
        current_key = manifest["current_key"]
        current_ideal = manifest["current_ideal"]
        orphaned = tuple(manifest["orphaned"])
        rekey_clean = manifest["rekey_clean"]
        rebirth_remapped = manifest["rebirth_remapped"]
    else:
        plan = _plan(repo)
        if not plan.needs_migration:
            return OpsV3Report(
                dry_run=False, changed=False, total_ops=0, rekey_clean=0, rebirth_remapped=0,
                orphaned=(), artifacts=(), dropped_refs=0,
                claims_orphaned=len(state.list_claim_files(repo)),
            )
        v3_ops, mapping, current_key, current_ideal = (
            plan.v3_ops, plan.mapping, plan.current_key, plan.current_ideal
        )
        orphaned = tuple(plan.orphaned)
        rekey_clean, rebirth_remapped = plan.rekey_clean, plan.rebirth_remapped
        state.save_json(repo, _MANIFEST, {
            "map": mapping, "orphaned": list(orphaned), "current_key": current_key,
            "current_ideal": current_ideal, "rekey_clean": rekey_clean,
            "rebirth_remapped": rebirth_remapped,
        })

    changed, dropped = _execute(repo, v3_ops, mapping, current_key, current_ideal)
    state.path(repo, _MANIFEST).unlink(missing_ok=True)  # crossing complete: retire the manifest
    return OpsV3Report(
        dry_run=False, changed=True, total_ops=len(mapping) + len(orphaned),
        rekey_clean=rekey_clean, rebirth_remapped=rebirth_remapped, orphaned=orphaned,
        artifacts=tuple(changed), dropped_refs=dropped,
        claims_orphaned=len(state.list_claim_files(repo)),
    )


def _changed_artifacts_preview(repo: Path, plan: _Plan) -> tuple[list[str], int]:
    """Which artifacts a `--apply` would rewrite, and how many op-id references it would drop --
    computed in memory (nothing written), by running each remap against the on-disk body and
    comparing. Mirrors `_execute`'s branches so the dry-run report is exact."""
    changed: list[str] = []
    dropped = 0
    v3_ops = plan.v3_ops
    mapping = _full_map(v3_ops, plan.mapping)

    old_table = state.load_json(repo, "ideal_table", default={})
    new_table, d = _remap_ideal_table(old_table, mapping, plan.current_key, plan.current_ideal, v3_ops)
    dropped += d
    if new_table != old_table:
        changed.append("ideal_table")

    old_journal = state.load_json(repo, "ideal_journal", default={})
    if old_journal:
        new_journal, d = _remap_journal(old_journal, mapping, v3_ops)
        dropped += d
        if new_journal != old_journal:
            changed.append("ideal_journal")

    old_forks = state.load_json(repo, "forks", default=None)
    if old_forks:
        new_forks, d = _remap_forks(old_forks, mapping)
        dropped += d
        if new_forks != old_forks:
            changed.append("forks")

    old_orset = state.load_json(repo, "declared_orset", default=None)
    if old_orset:
        new_orset, d = _remap_orset(old_orset, mapping)
        dropped += d
        if new_orset != old_orset:
            changed.append("declared_orset")

    old_staged = state.load_json(repo, "staged", default=None)
    if old_staged:
        new_staged, d = _remap_staged(old_staged, mapping, v3_ops)
        dropped += d
        if new_staged != old_staged:
            changed.append("staged")

    if Store(repo).all_hollow_ops():
        changed.append("hollows")

    old_drafts = state.load_json(repo, "drafts", default=None)
    if old_drafts:
        changed.append("drafts")

    if state.list_proposal_files(repo):
        changed.append("proposals")

    return changed, dropped
