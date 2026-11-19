"""The JSON views the CLI's `--json` mode and the MCP server render. Views read an already-mined
store; mining (`ugh.core.lens.get`) is the caller's job. Every step carries its address.

* `log_view`     -- the saves, newest first, each with its steps.
* `show_view`    -- an address's steps, the ask behind each save it touches and what the check
  observed on it, and the planner's answer.
* `find_view`    -- names, subjects, definitions and asks matching some words, as addresses.
* `plan_view`    -- each declared name with the saves since the baseline carrying it, and the steps
  carrying no declared name.
* `status_view`  -- counts, coverage, what the check observed on HEAD, drift, the pending
  two-version choices and the plan.
* `state_view`   -- the current ref's ideal: frontier and coverage.
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
    """An address's steps, the saves they came from with the ask behind each and what the check
    observed on each (`observe.of_commit`, None when not observed), and the planner's answer: what
    taking them out would take, or bringing them back when every step is out."""
    from ugh.address import AddressError, Record, resolve
    from ugh.core import observe, planner
    from ugh.intent.stint import asks_for_ops, dominant_ask

    rec = Record(repo)
    try:
        steps = resolve(repo, token, rec)
    except AddressError as e:
        return {"ok": False, "address": token, "message": str(e), "hits": e.hits}
    rows = rec.steps(steps, detail=True)
    saves = []
    for sha in sorted({st.sha for st in steps}, key=rec.ctx.index.__getitem__):
        ask = dominant_ask(asks_for_ops(repo, rec.ops(st for st in steps if st.sha == sha), [sha]))
        saves.append({**rec.save_row(sha), "ask": ask,
                      "observed": observe.of_commit(repo, sha, rec.ctx.parent[sha])})
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
    """HEAD's ideal: the paths `code(I)` covers and the fraction of them with at least one live
    definition at the frontier (1.0 when nothing is covered). `full=True` adds the frontier map and
    the entity paths in place of their counts."""
    from ugh.core import opindex
    from ugh.core.fold import _symbol_kind
    from ugh.core.lens import ideal_for_ref
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

    base = {
        "covered_paths": sorted(covered),
        "coverage_fraction": (len(entity_paths) / len(covered)) if covered else 1.0,
        "derived_paths": sorted(p for p in covered if tiers.is_derived(p)),
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
    """The relative paths the previous write into `out` listed; an unreadable manifest is empty."""
    import json

    try:
        data = json.loads((out / FOLD_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {p for p in data.get("paths", []) if isinstance(p, str)}


def _prune_fold_leftovers(out, stale: set[str]) -> list[str]:
    """Delete the manifest-listed paths this fold no longer contains and the directories that
    emptied. The manifest is an editable file, so a symlink or a path outside `out` is skipped."""
    deleted: list[str] = []
    for rel in sorted(stale):
        full = out / rel
        try:
            full.resolve().relative_to(out.resolve())
        except (OSError, ValueError):
            continue
        if full.is_symlink() or not full.is_file():
            continue
        full.unlink()
        deleted.append(rel)
    # Deepest first, so a directory emptied by removing its own subdirectory is itself collected.
    for rel in sorted(deleted, key=lambda p: p.count("/"), reverse=True):
        parent = (out / rel).parent
        while parent != out and parent.is_dir():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    return deleted


def _sync_fold_dir(out_dir, materialized: dict[str, bytes], op_count: int) -> dict:
    """Write `materialized` into `out_dir`, delete what the previous write left that it no longer
    contains, and rewrite the manifest."""
    import json
    from pathlib import Path

    out = Path(out_dir)
    previous = _read_fold_manifest(out)

    out.mkdir(parents=True, exist_ok=True)
    for path, data in sorted(materialized.items()):
        full = out / path
        full.parent.mkdir(parents=True, exist_ok=True)
        # Unchanged files are not rewritten: a watching dev server reacts to mtimes, not contents.
        try:
            if full.read_bytes() == data:
                continue
        except OSError:
            pass
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
    """Write the tree a revert or restore plan would leave into `out_dir`: the current fold with
    the plan's files changed."""
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
    """The paths whose working-tree bytes do not hash to the manifest's `{path: sha256}`."""
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
    """File and symbol counts, coverage (`state_view`'s), what the check observed on HEAD
    (`observed`: None when no check is configured, else `{head}` as in a plan), drift (paths
    whose bytes differ from `code(current_ideal)`, that is unsaved edits), the pending two-version
    choices and the plan."""
    from hashlib import sha256 as _sha256

    from ugh import state as state_mod
    from ugh.core import observe, opindex
    from ugh.core.fold import code
    from ugh.core.lens import _ids_digest, current_ideal, ops_with_frontier_images, sync_status
    from ugh.core.op import MINER_VERSION, is_bottom
    from ugh.store.gitbind import GitBinding

    st = state_view(repo)
    ideal = current_ideal(repo)
    # drift needs only each path's hash, so an unchanged ideal reuses the manifest, loading no images
    ideal_digest = f"{MINER_VERSION}:{_ids_digest(ideal.op_ids)}"
    manifest = state_mod.load_json(repo, "mat_manifest", default=None)
    if isinstance(manifest, dict) and manifest.get("digest") == ideal_digest:
        path_hashes = manifest["paths"]
    else:
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
        # From `current_ideal`, like the symbol count; `state_view` uses HEAD's, which can differ.
        "files": len(ideal.covered_paths(index)),
        "symbols": symbol_count,
        "coverage_fraction": st["coverage_fraction"],
        "observed": ({"head": observe.summary(observe.load(repo, GitBinding(repo).head()))}
                     if observe.configured(repo) else None),
        "drift": {"any": bool(drift), "paths": drift},
        "choices": choices_view(repo),
        "sync_status": sync_status(repo),
        "plan": plan_view(repo),
    }
