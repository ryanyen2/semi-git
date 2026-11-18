"""The canonical JSON projection of ugh -- one schema, many clients.

Every machine-readable surface (the CLI's `--json` mode and the MCP server) renders the dicts
produced here. Reads are offline and pure over an already-mined store; mining on contact
(`ugh.core.lens.get`) is the caller's job. Every step carries its address (`ugh.address`).

* `log_view`     -- the saves, newest first, each with its steps.
* `show_view`    -- an address's steps, the ask behind each save it touches, and the planner's answer.
* `find_view`    -- names, subjects, definitions and asks matching some words, as addresses.
* `plan_view`    -- each declared name with the saves since the baseline carrying it, and the steps
  carrying no declared name.
* `status_view`  -- counts, coverage, the oracle, drift, untracked files, forks and the plan.
* `state_view`   -- the current ref's ideal: frontier, coverage, the oracle's verdict.
* `sync_view`, `sync_preview_view`, `forks_view`, `fork_detail_view` -- sync and its forks.
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


_REVERSIBLE_VERBS = frozenset({"revert", "restore"})


def _reversible(verb: str) -> bool:
    """Whether a verb's effect can be walked back with `ugh undo` -- the ideal-edit verbs -- or is
    a shared/one-way advance (`sync`, default False)."""
    return verb in _REVERSIBLE_VERBS


def so_what_for(projected: dict) -> str:
    """The one-line consequence of a sync or resolve preview -- what happens, whether it can be
    undone. Pure over the projection dict."""
    verb = projected.get("verb", "")
    target = projected.get("target")
    # `__anchor__`/`__residue__` are the miner's own bookkeeping symbols, and they sort ahead of
    # the real one in the same file. Naming one here put "b.py::__anchor__::user will break" in
    # front of a user, which reads as an internal leak rather than a consequence, so the fallback
    # skips them and only uses one if a preview genuinely touched nothing else.
    touched = projected.get("affected_symbols") or []
    real = [s for s in touched if "::__" not in s]
    primary = (
        target if target and "::" in target
        else (real or touched or [None])[0] or target or "this"
    )
    undo = ("Undo-able." if projected.get("reversible", _reversible(verb))
            else "Not auto-undoable — review carefully.")

    if verb == "sync":
        src = f"{projected.get('remote') or 'the remote'}/{projected.get('target') or 'the branch'}"
        if projected.get("up_to_date"):
            return f"Already up to date with {src} — nothing to fold in."
        n = projected.get("ops_added", 0)
        forks = projected.get("forks") or []
        if forks:
            return (f"Folds in {n} op(s) from {src}; {len(forks)} fork(s) surface for you to "
                    f"resolve — no work is lost, they wait at the common ancestor. {undo}")
        return f"Folds in {n} op(s) from {src} — footprint-disjoint, no forks. {undo}"

    if verb == "resolve":
        sym = projected.get("target") or "the symbol"
        if not projected.get("clean", True):
            return f"Can't resolve {sym} yet — {projected.get('error') or 'no open fork'}."
        return (f"Resolves the fork on {sym}: takes your merged edit, runs the oracle, then lands "
                f"it (closes the fork). {undo}")

    if not projected.get("ok", True):
        if projected.get("forked"):
            return (f"Won't apply — {verb} of {primary} would fork it. "
                    f"Resolve the fork first (ugh resolve {primary}).")
        return f"Won't apply — {projected.get('message') or 'refused'}."

    return f"{verb} {primary}. {undo}"


def _side_entity_spans(path: str, source: bytes) -> list[dict]:
    """One side of a verb projection, as `{symbol, kind, start_line, end_line}` in document order.

    The same tree-sitter extraction `_entity_line_spans` runs against the current ideal, pointed at
    an arbitrary folded text instead. A client showing "what did this chapter change" has a
    before/after pair and wants the answer per entity, not per file -- and it cannot find where a
    function begins by looking at the text, which is the whole reason this repo parses in the first
    place. Both sides are spanned because the two texts have different line numbers: a caller maps
    a line on either side to the entity that owns it. Never raises (`extract_file` answers `[]` for
    an unsupported or unparseable file), so a projection stays complete even where it has no
    entities to name.
    """
    from ugh.entities.extract import extract_file

    return [
        {"symbol": e.id, "kind": e.kind, "start_line": e.start_line, "end_line": e.end_line}
        for e in sorted(extract_file(path, source), key=lambda e: (e.start_line, e.id))
    ]


def _project_sync_preview(repo, plan) -> dict:
    """Project a `ugh.core.sync.SyncPlan` dry-run into the shared consequence shape. Unlike `land`, a
    sync fork does not block -- the fork-free part still merges and the fork surfaces as state -- so
    `forks` here is *what would surface*, and `ok` is False only to flag that attention is needed,
    not that nothing happens. No clean-tree precondition, so `clean` is always True."""
    projected = {
        "ok": not plan.forks,
        "verb": "sync",
        "target": plan.branch,
        "remote": plan.remote,
        "affected_symbols": [f[0] for f in plan.forks],
        "forked": bool(plan.forks),
        "files": {},
        "message": "",
        "fallout": [],
        "carry_count": 0,
        "reversible": False,
        "clean": True,
        "up_to_date": plan.up_to_date,
        "ops_added": plan.ops_added,
        "forks": [list(t) for t in plan.forks],
        "name_conflicts": _name_conflicts(repo, plan.name_conflicts),
        "base_recovery": plan.base_recovery,
        "theirs_recovery": plan.theirs_recovery,
    }
    projected["so_what"] = so_what_for(projected)
    return projected


def sync_preview_view(repo, remote: str | None = None, branch: str | None = None) -> dict:
    """The dry-run consequence of `ugh sync [remote] [branch]` -- what folding the teammate's branch
    in *would* bring (incoming op count, any fork that would surface, recovery modes), computed
    side-effect-free (`sync.plan_sync`: `fetch -> ingest -> resolve`, no `materialize`, rolled back
    to leave no trace). A pure read the CLI shows as a feedforward pane before the merge lands."""
    from ugh.core import sync as sync_mod

    plan = sync_mod.plan_sync(repo, remote=remote, branch=branch)
    return _project_sync_preview(repo, plan)


def resolve_apply_preview_view(repo, symbol: str) -> dict:
    """The dry-run consequence of `ugh resolve <symbol> --apply`: take the edited tree's version,
    run the oracle, commit it, closing the fork. `clean=False` with a reason when there is no open
    fork for `symbol`. A pure read."""
    from ugh.config import load_oracle_config

    fork = next((f for f in forks_view(repo)["forks"] if f["symbol"] == symbol), None)
    error = None if fork is not None else f"no open fork for {symbol!r} — `ugh status` lists the open forks"
    clean = error is None

    projected = {
        "ok": clean,
        "verb": "resolve",
        "target": symbol,
        "affected_symbols": [symbol],
        "forked": False,
        "files": {},
        "message": error or "",
        "fallout": [],
        "carry_count": 0,
        "reversible": False,
        "clean": clean,
        "error": error,
        "oracle_configured": load_oracle_config(repo) is not None,
        "tips": list(fork["tips"]) if fork else [],
    }
    projected["so_what"] = so_what_for(projected)
    return projected


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
    """Project an already-run `ugh.core.sync.SyncReport` (plan U15/U20) -- `sync` performs a real
    git fetch/merge/commit, so unlike this module's other views it isn't a pure read the CLI can
    call on demand; the CLI runs `sync.sync(...)` itself and hands the result here for projection.
    `open_fork_count` (additive, U20/C4) is the divergence-as-state loudness signal: nonzero means
    the fork-free part merged but that many same-symbol forks are recorded and await resolution."""
    return {
        "remote": report.remote,
        "branch": report.branch,
        "merged": report.merged,
        "message": report.message,
        "fetched_sha": report.fetched_sha,
        "merge_sha": report.merge_sha,
        "ops_added": report.ops_added,
        "forks": [list(triple) for triple in report.forks],
        "open_fork_count": len(report.forks),
        "base_recovery": report.base_recovery,  # U7/R12: how the merge-base ideal was recovered
        "theirs_recovery": report.theirs_recovery,  # ...and theirs' tip; "none" = a refused claim
        "name_conflicts": _name_conflicts(repo, report.name_conflicts),
    }


def _open_fork_records(repo) -> list:
    """The `.sgt/forks.json` records the read/surfacing path should show: filtered to *resolvable*
    forks (`order.resolvable_forks`) -- real symbols whose two tips genuinely diverge. Synthetic
    `__anchor__`/`__residue__` collisions and same-after pseudo-forks (a revert onto identical bytes,
    or an add/delete/re-add rebirth) are neutralized by `fork_free` and carry no `ugh resolve` remedy,
    so they never surface. This also cleans records persisted *before* the write-side filter landed
    (`_record_parked_forks` is union-only and never removes), and drops any record whose tip is no
    longer in the store (`Store.get` -> None). Decodes only the two tip ops per record -- no full
    `all_ops()` scan.

    Stamps each surviving record with `cross_version`: True when its two tips were mined under
    *different* MINER_VERSIONs (F82). A version bump re-mines history but nothing evicts the previous
    generation, so the same commit can sit in the store twice; the two generations disagree about a
    symbol's after-state and collide on its `before_version`. `fork_free` drops both tips and their
    up-sets exactly as for a real divergence -- all 612 records on ugh's own store, costing 91% of the
    ideal -- so these must stay visible. But nobody edited anything and no hand-merge closes them:
    the only remedy is `ugh advanced migrate ops-v3`. Stamped here rather than in one caller because
    two surfaces read this list (`forks_view`, `status_view`) and each phrased it
    itself -- the flag has to arrive with the record or a surface silently keeps the old wording."""
    from ugh import state
    from ugh.core.order import resolvable_forks
    from ugh.core.store import Store

    records = state.load_json(repo, "forks", default=[])
    valid = [r for r in records if len(r.get("tips", ())) == 2]
    if not valid:
        return []
    store = Store(repo)
    by_id = {tip: store.get(tip) for r in valid for tip in r["tips"]}
    keep = {
        (s, a, b)
        for s, a, b in resolvable_forks(
            [(r["symbol"], r["tips"][0], r["tips"][1]) for r in valid], by_id
        )
    }
    out = []
    for r in valid:
        if (r["symbol"], r["tips"][0], r["tips"][1]) not in keep:
            continue
        versions = {op.miner_version for op in (by_id[t] for t in r["tips"]) if op is not None}
        out.append({**r, "cross_version": len(versions) > 1})
    return out


def forks_view(repo) -> dict:
    """The open same-symbol forks a prior sync recorded in committed `.sgt/forks.json` (plan U20,
    C4) -- for `ugh forks`. Each fork carries its symbol, its two tips, and the `ugh merge-op`
    remedy that closes it, plus the cheap-to-derive `file` it lives in (`symbol.split("::", 1)[0]`).
    There's no single "current" line span to add beyond that: both tips are, by construction,
    excluded from every verb-visible ideal, so a resolution UI that needs each tip's own content
    calls `fork_detail_view` instead. Filtered to resolvable forks via `_open_fork_records` (light
    per-tip store reads, each carrying `cross_version`); empty (`{"open": 0, "forks": []}`) when there
    are none."""
    records = _open_fork_records(repo)
    return {
        "open": len(records),
        "forks": [{**r, "file": r["symbol"].split("::", 1)[0]} for r in records],
    }


def fork_detail_view(repo, symbol: str) -> dict:
    """Per-tip folded images for one open same-symbol fork (plan U20, C4) -- `ugh resolve`'s side-by-side pick.
    Each tip is folded on its own downward closure (`order.downset`, over the whole op universe --
    correct here because a fork's two tips are siblings, never each other's predecessor, so a tip's
    own downset structurally excludes the other tip and anything reachable only through it) via
    `Ideal.from_ops` + `code`, so a resolution UI can diff both tips' full file content without a
    separate frontier query per tip. `{"error": ...}` when the symbol has no open (resolvable) fork."""
    from ugh.core.fold import code
    from ugh.core.ideal import Ideal
    from ugh.core.order import downset
    from ugh.core.store import Store

    records = _open_fork_records(repo)
    record = next((r for r in records if r["symbol"] == symbol), None)
    if record is None:
        return {"error": f"no open fork for {symbol!r}", "symbol": symbol}

    ops = Store(repo).all_ops()
    tips = []
    for tip in record["tips"]:
        ideal = Ideal.from_ops(downset(tip, ops), ops)
        materialized = code(ideal, ops)
        tips.append({
            "op_id": tip,
            "files": {path: content.decode("utf-8", "replace") for path, content in materialized.items()},
        })
    return {"symbol": symbol, "tips": tips, "remedy": record["remedy"]}


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
    (reusing `state_view`'s definition), the oracle's overall status, and working-tree drift --
    paths whose on-disk bytes no longer match `code(current_ideal)` (e.g. an edit made outside
    `ugh`, or a verb applied without re-writing the working tree)."""
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

    from ugh.core.lens import materialization_skips

    drift = _drift_paths_by_hash(repo, path_hashes)
    # `path_hashes` stands in for the materialized dict: the skips read consults it for path
    # MEMBERSHIP only (which tracked paths the ideal doesn't cover). `None` as `all_ops`: the
    # backstop read folds the *maximal* ideal -- let it load (path-restricted) images itself
    # (rare: only when tracked paths would be deleted).
    skips = materialization_skips(repo, path_hashes, None)
    open_forks = _open_fork_records(repo)

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
        # R3/R4: paths a materializing verb refuses to touch -- symlinks (unmanaged) and files the
        # current ideal dropped whose live bytes no valid ideal can regenerate (backstop-kept).
        "unmanaged": skips["unmanaged"],
        "backstop_kept": skips["backstop_kept"],
        # Files ugh holds no op for at all: kept, but not damage and not repairable.
        "never_recorded": skips.get("never_recorded", []),
        "forks": {"open": len(open_forks), "records": open_forks},
        "sync_status": sync_status(repo),
        "plan": plan_view(repo),
    }
