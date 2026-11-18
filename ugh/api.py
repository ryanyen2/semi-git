"""The canonical JSON projection of ugh -- one schema, many clients.

Every machine-readable surface (the CLI's `--json` mode and the MCP server) renders the dicts
produced here. Reads are offline and pure over an already-mined store; mining on contact
(`ugh.core.lens.get`) is the caller's job. Every step carries its address (`ugh.address`).

* `log_view`     -- the saves, newest first, each with its steps.
* `show_view`    -- an address's steps, the ask behind each save it touches, and the planner's answer.
* `find_view`    -- names, subjects, definitions and asks matching some words, as addresses.
* `plan_view`    -- each declared name with the saves since the baseline carrying it, and the steps
  carrying no declared name.
* `status_view`  -- counts, coverage, the oracle, drift, the pending two-version choices and the plan.
* `state_view`   -- the current ref's ideal: frontier, coverage, the oracle's verdict.
* `sync_view`    -- what a sync did.
* `choices_view` -- the pending two-version choices, each with its address.
"""

from __future__ import annotations


def plural(n: int, noun: str) -> str:
    """`3 symbols`, `1 symbol`, for the regular nouns ugh counts."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def log_view(repo, *, limit: int | None = None) -> dict:
    """The saves, newest first: `{"saves": [{"sha", "subject", "date", "steps"}]}`, each step
    `{"address", "kind", "name", "out"}`."""
    from ugh.address import Record

    rec = Record(repo)
    shas = rec.saves()[:limit] if limit else rec.saves()
    return {"saves": [{**rec.save_row(sha), "steps": rec.steps(rec.ctx.of_commit.get(sha, ()))}
                      for sha in shas]}


def show_view(repo, token: str) -> dict:
    """An address's steps, the saves they came from with the ask behind each, and the planner's
    answer: what taking them out would take, or bringing them back when every step is out."""
    from ugh.address import AddressError, Record, resolve
    from ugh.core import planner
    from ugh.intent.stint import asks_for_ops, dominant_ask

    rec = Record(repo)
    try:
        steps = resolve(repo, token, rec)
    except AddressError as e:
        return {"ok": False, "address": token, "message": str(e), "hits": e.hits}
    rows = rec.steps(steps)
    saves = []
    for sha in sorted({st.sha for st in steps}, key=rec.ctx.index.__getitem__):
        ask = dominant_ask(asks_for_ops(repo, rec.ops(st for st in steps if st.sha == sha), [sha]))
        saves.append({**rec.save_row(sha), "ask": ask})
    verb = "restore" if all(row["out"] for row in rows) else "revert"
    p = planner.plan(repo, steps, verb=verb, target=token, removal_set=verb == "revert")
    return {"ok": True, "address": token, "steps": rows, "saves": saves, "plan": planner.to_json(p)}


def find_view(repo, query: str, *, limit: int = 10) -> dict:
    """`{"query", "hits": [{"address", "matched", "text"}]}`: names, save subjects, definition names
    and captured asks containing the query's words."""
    from ugh.address import find

    return {"query": query, "hits": find(repo, query, limit=limit)}


def plan_view(repo) -> dict | None:
    """The plan: each declared name with the saves since the baseline that carry it, then the steps
    since the baseline that carry no declared name. None when nothing is declared."""
    from ugh import state
    from ugh.address import Record

    plan = state.load_json(repo, "plan", default=None)
    if not plan:
        return None
    rec = Record(repo)
    since = set(rec.saves())
    if plan["baseline"]:
        since &= {sha for sha, _p, _s in rec.gb.history(plan["baseline"])}
    steps = [st for st in rec.ctx.steps if st.sha in since]
    declared = []
    for label in plan["names"]:
        shas = sorted({st.sha for st in steps if rec.name(st) == label}, key=rec.ctx.index.__getitem__)
        declared.append({"name": label, "saves": [rec.save_row(sha) for sha in shas]})
    other = rec.steps(st for st in steps if rec.name(st) not in plan["names"])
    return {"baseline": plan["baseline"], "names": declared, "other": other}


def state_view(repo, *, full: bool = False) -> dict:
    """The current ref's ideal: its per-chain frontier (symbol -> tip op id), the paths
    `code(I)` covers, R7's entity-granularity coverage fraction, and the async oracle's verdict
    (U9) -- `oracle_verdict` is `None` until `ugh oracle run` has recorded one for this exact
    ideal, or always `None` when `oracle_configured` is False (no `.sgt/oracle.json`).

    Coverage-fraction definition (R7): of the paths present in the materialized tree, the
    fraction carried at *entity* granularity -- a path with at least one live top-level or nested
    entity symbol at the frontier (a parseable def/class/method ugh can revert or cherry-pick on
    its own) -- versus paths represented only by a whole-file pseudo-symbol or by module-level
    residue / layout facts (coarse, file-granularity coverage). `entity_paths` is that numerator
    as an explicit list, so `covered_paths` minus `entity_paths` is exactly the whole-file-only
    remainder. A ref with nothing covered reports 1.0 (vacuously: nothing is stuck at whole-file
    granularity).

    Compact by default: drops the per-chain `frontier` map and the `entity_paths` list (one
    entry per symbol/path -- unbounded on a large ideal) in favor of their counts.
    `covered_paths`/`coverage_fraction`/`oracle_*` stay in both modes -- `status_view` reads
    them, never `frontier`/`entity_paths`, so it can keep calling this at its default. `full=True`
    restores `frontier` and `entity_paths`."""
    from ugh.config import load_oracle_config
    from ugh.core import opindex
    from ugh.core.fold import _symbol_kind
    from ugh.core.lens import ideal_for_ref
    from ugh.core.oracle import verdict_for
    from ugh.core.op import is_bottom

    ops = opindex.index_ops(repo)
    ideal = ideal_for_ref(repo, "HEAD")
    frontier = ideal.frontier(ops)
    by_id = {op.id: op for op in ops}

    covered = ideal.covered_paths(ops)
    entity_paths: set[str] = set()
    for sym, op_id in frontier.items():
        after = by_id[op_id].footprint[sym][1]
        if not is_bottom(after) and _symbol_kind(sym) in ("entity", "nested"):
            entity_paths.add(sym.split("::", 1)[0])

    from ugh.core import tiers

    oracle_configured = load_oracle_config(repo) is not None
    base = {
        "covered_paths": sorted(covered),
        "coverage_fraction": (len(entity_paths) / len(covered)) if covered else 1.0,
        "derived_paths": sorted(p for p in covered if tiers.is_derived(p)),  # S4/U27
        "oracle_configured": oracle_configured,
        "oracle_verdict": verdict_for(repo, ideal) if oracle_configured else None,
    }
    if full:
        return {
            "frontier": {sym: frontier[sym] for sym in sorted(frontier)},
            "entity_paths": sorted(entity_paths),
            **base,
        }
    return {
        "frontier_count": len(frontier),
        "entity_path_count": len(entity_paths),
        **base,
    }


def _name_conflicts(repo, conflicts) -> list[dict]:
    """`{address, ours, theirs}` for each step whose name is an op both sides of a sync relabelled
    differently (the op's id when it names no step here)."""
    if not conflicts:
        return []
    from ugh.address import Record

    rec = Record(repo)
    return [{"address": address, "ours": ours, "theirs": theirs} for op, ours, theirs in conflicts
            for address in [rec.step(st)["address"] for st in rec.ctx.steps if op in rec.carriers(st)]
            or [op[:12]]]


def sync_view(repo, report) -> dict:
    """An already-run `ugh.core.sync.SyncReport`: what came in, the units left as two-version
    choices (by address) and the names both sides relabelled differently."""
    from ugh.core.op import unit_address

    return {
        "ok": True,
        "remote": report.remote,
        "branch": report.branch,
        "message": report.message,
        "head": report.head,
        "choices": [unit_address(u) for u in report.choices],
        "name_conflicts": _name_conflicts(repo, report.name_conflicts),
    }


def choices_view(repo) -> list[dict]:
    """The pending two-version choices: `{address, unit, merge}`, theirs being the merge's second
    parent."""
    from ugh.core import sync

    return [{"address": c.address, "unit": c.unit, "merge": c.merge} for c in sync.choices(repo)]


FOLD_MANIFEST = ".sgt-fold-manifest.json"


def _read_fold_manifest(out) -> set[str]:
    """The relative paths the previous `fold_out_view` run wrote into `out`. Missing, unreadable,
    or malformed reads as "no previous run", which makes the next run write-only."""
    import json

    try:
        data = json.loads((out / FOLD_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {p for p in data.get("paths", []) if isinstance(p, str)}


def _prune_fold_leftovers(out, stale: set[str]) -> list[str]:
    """Delete exactly the manifest-listed paths this fold no longer contains, then remove the
    directories that deletion emptied. Two containment guards, because the manifest is a file on
    disk that something else could have edited: never follow a symlink, and never touch anything
    that doesn't resolve inside `out`. A directory that still holds anything at all survives."""
    deleted: list[str] = []
    for rel in sorted(stale):
        full = out / rel
        try:
            full.resolve().relative_to(out.resolve())
        except (OSError, ValueError):
            continue  # escapes the overlay -- not ours to delete
        if full.is_symlink() or not full.is_file():
            continue
        full.unlink()
        deleted.append(rel)
    # Deepest first, so a directory emptied by removing its own subdirectory is itself collected.
    for rel in sorted(deleted, key=lambda p: p.count("/"), reverse=True):
        parent = (out / rel).parent
        while parent != out and parent.is_dir():
            try:
                parent.rmdir()  # only ever succeeds on a genuinely empty directory
            except OSError:
                break
            parent = parent.parent
    return deleted


def _sync_fold_dir(out_dir, materialized: dict[str, bytes], op_count: int) -> dict:
    """Write `materialized`'s raw bytes into `out_dir`, remove the manifest-listed paths this fold
    no longer contains, and re-write the manifest. The overlay-sync half of `fold_out_view`, split
    out so `verb_result_out_view` renders a preview's own counterfactual through exactly the same
    write/prune/inventory rules rather than a second copy of them."""
    import json
    from pathlib import Path

    out = Path(out_dir)
    previous = _read_fold_manifest(out)

    out.mkdir(parents=True, exist_ok=True)
    for path, data in sorted(materialized.items()):
        full = out / path
        full.parent.mkdir(parents=True, exist_ok=True)
        # Skip the write when the bytes already match. A dev server watching this directory sees
        # mtimes, not contents: Vite restarts the whole server when `vite.config.ts` is touched
        # and clears its TypeScript cache when `tsconfig.json` is, so rewriting every file turns
        # each scrub step into a restart instead of a hot replace of the few files that differ.
        try:
            if full.read_bytes() == data:
                continue
        except OSError:
            pass  # absent, unreadable, or a directory -- let the write decide
        full.write_bytes(data)

    deleted = _prune_fold_leftovers(out, previous - set(materialized))
    (out / FOLD_MANIFEST).write_text(
        json.dumps({"paths": sorted(materialized)}, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "ok": True,
        "path": str(out),
        "written": len(materialized),
        "deleted": len(deleted),
        "op_count": op_count,
    }


def verb_result_out_view(repo, plan, out_dir) -> dict:
    """Write the tree a revert/restore plan describes onto `out_dir`, through `_sync_fold_dir`'s
    overlay rules: the current fold with the plan's files changed (a planned edit writes nothing
    else)."""
    from ugh.core.fold import code
    from ugh.core.lens import current_ideal, ops_with_frontier_images

    ideal = current_ideal(repo)
    tree = code(ideal, ops_with_frontier_images(repo, ideal))
    for path, (_before, after) in plan.files.items():
        if after is None:
            tree.pop(path, None)
        else:
            tree[path] = after
    return _sync_fold_dir(out_dir, tree, len(ideal.op_ids))


def _drift_paths_by_hash(repo, path_hashes: dict[str, str]) -> list[str]:
    """`_drift_paths` against a `{path: sha256-hex}` manifest instead of materialized bytes --
    same answer (hash equality stands in for byte equality), no op images loaded."""
    from hashlib import sha256
    from pathlib import Path

    repo_path = Path(repo)
    drift = []
    for path, expected in path_hashes.items():
        full = repo_path / path
        actual = sha256(full.read_bytes()).hexdigest() if full.is_file() else None
        if actual != expected:
            drift.append(path)
    return sorted(drift)


def status_view(repo) -> dict:
    """A kernel-backed summary (plan U13): file and symbol counts, R7's coverage fraction
    (reusing `state_view`'s definition), the oracle's overall status, working-tree drift (paths
    whose on-disk bytes no longer match `code(current_ideal)`, i.e. edits not saved yet), the
    pending two-version choices and the plan."""
    from hashlib import sha256 as _sha256

    from ugh import state as state_mod
    from ugh.core import opindex
    from ugh.core.fold import code
    from ugh.core.lens import _ids_digest, current_ideal, ops_with_frontier_images, sync_status
    from ugh.core.op import MINER_VERSION, is_bottom
    from ugh.core.oracle import overall_status

    st = state_view(repo)
    ideal = current_ideal(repo)
    # Materialization manifest (`.sgt/local/mat_manifest.json`): `{path: sha256}` of
    # `code(current_ideal)`, keyed by the ideal's id-set digest + miner version. Drift detection
    # and the skips read only need byte-EQUALITY per path and path MEMBERSHIP -- both answered
    # by the manifest -- so a repeat `status` over an unchanged ideal skips loading the frontier
    # producers' images entirely (~3.5k op-file reads here). Any ideal movement changes the
    # digest; op content is fixed by the content-addressed ids, so the digest fixes the fold.
    ideal_digest = f"{MINER_VERSION}:{_ids_digest(ideal.op_ids)}"
    manifest = state_mod.load_json(repo, "mat_manifest", default=None)
    if isinstance(manifest, dict) and manifest.get("digest") == ideal_digest:
        path_hashes = manifest["paths"]
    else:
        # Frontier-selective read: this view folds only `ideal`, so it needs images for exactly
        # the frontier producers -- not `Store.all_ops()`'s every-op images decode.
        ops = ops_with_frontier_images(repo, ideal)
        materialized = code(ideal, ops)
        path_hashes = {p: _sha256(b).hexdigest() for p, b in materialized.items()}
        state_mod.save_json(repo, "mat_manifest", {"digest": ideal_digest, "paths": path_hashes})
    index = opindex.index_ops(repo)
    by_id = {op.id: op for op in index}
    symbol_count = sum(
        1 for sym, op_id in ideal.frontier(index).items() if not is_bottom(by_id[op_id].footprint[sym][1])
    )

    drift = _drift_paths_by_hash(repo, path_hashes)

    return {
        # Count files from the *same* `current_ideal` the symbol count comes from, not
        # `state_view`'s HEAD ideal -- on an init-only repo (no ugh commit advancing HEAD) the two
        # diverge, which read as the nonsensical "0 file(s), N symbol(s)".
        "files": len(ideal.covered_paths(index)),
        "symbols": symbol_count,
        "coverage_fraction": st["coverage_fraction"],
        "oracle": {
            "configured": st["oracle_configured"],
            "status": overall_status(st["oracle_verdict"]) if st["oracle_configured"] else "unconfigured",
        },
        "drift": {"any": bool(drift), "paths": drift},
        "choices": choices_view(repo),
        "sync_status": sync_status(repo),
        "plan": plan_view(repo),
    }
