"""Porcelain completion (plan 002, U26; design doc §1 / D2, D3).

Two things live here, both small and both *data*:

1. **The `sgt git` refusal table (D2).** `REFUSALS` maps each tree-mutating git subcommand to the
   sgt verb that does the job natively. `sgt git <sub> …` for such a subcommand is refused (never
   run) with that remedy named, because a raw `git checkout`/`reset`/`merge` rewrites the working
   tree behind sgt's back and the op store drifts. Appending `--force` overrides the refusal (the
   token is consumed, git runs the plain command, and the `gitbind` out-of-band detector re-mines
   on next contact -- the net that catches raw `git` too). This replaces U18's advisory-only
   posture; the routing table is one data table, not conditionals scattered through the passthrough.

2. **The daily-loop verbs (D3): `switch`, `save`, `undo`.** Exactly three, each composed from
   existing lens machinery (no new kernel call): `switch` materializes a named ideal (an existing
   branch's committed tree) via `get` + git checkout + `get`; `save` is the put-path sugar
   (`get` + witness commit + `record_ideal`); `undo` inverts the last mutating operation by popping
   the unified operation log (`oplog.undo`), whatever its kind. Everything git does that sgt does not wrap
   stays one keystroke away behind `sgt git …`; a verb is only wrapped where the sgt-native version
   is *semantically different*, never merely renamed (design doc §1 non-goal).
"""

from __future__ import annotations

from ._common import _emit_json, _fail, _fail_json

# D2 routing table (design doc §1): git subcommand -> the sgt verb that owns that job. A subcommand
# here is refused by `sgt git`; anything absent passes through untouched (read/inspect verbs, and
# plumbing/config like `remote add`/`config`/`tag`/`bisect`/`fsck`). `pull`/`merge` route to `sgt
# sync` (integration is op-set union + fork-surfacing, not a text merge); `reset` to `sgt undo`;
# `revert` to `sgt revert`; `stash` dissolves into `sgt save` (a dirty tree is just ops not yet
# landed). `rebase` has no sgt analogue -- history is a mined DAG -- so it routes to `sgt sync`.
REFUSALS: dict[str, str] = {
    "checkout": "sgt switch <branch>  (or `sgt restore <path>` to restore files)",
    "switch": "sgt switch <branch>",
    "restore": "sgt restore <ref>",
    "pull": "sgt sync [remote] [branch]",
    "merge": "sgt sync [remote] [branch]",
    "reset": "sgt undo  (or `sgt revert <ref>` to drop an op)",
    "rebase": "sgt sync  (history is a mined op DAG; sgt has no rebase)",
    "revert": "sgt revert <ref>",
    "cherry-pick": "sgt restore <ref>  (or `sgt advanced transplant <op>... --onto <ref>`)",
    "stash": "sgt save  (a dirty tree is just ops not yet landed)",
    "am": "git apply, then `sgt save` to record the change as ops",
}


def git_remedy(subcommand: str) -> str | None:
    """The sgt remedy for a refused git subcommand, or None if `sgt git <subcommand>` passes
    through. The single decision point the passthrough consults (D2 as data)."""
    return REFUSALS.get(subcommand)


def refusal_message(subcommand: str, remedy: str) -> str:
    """The stderr refusal text: what sgt refused, the named remedy, and the `--force` escape."""
    return (
        f"sgt: `git {subcommand}` rewrites the working tree behind sgt's tracking -- use `{remedy}`\n"
        f"     (append --force to run git anyway; sgt re-mines on next contact)\n"
    )


def register(subs, parent) -> None:
    sw = subs.add_parser("switch", parents=[parent])
    sw.add_argument("branch")
    sw.set_defaults(func=_cmd_switch)

    sv = subs.add_parser("save", parents=[parent])
    sv.add_argument("-m", "--message", dest="message")
    sv.add_argument("--as", dest="as_label", metavar="LABEL",
                    help="name the feature this save's work lands in -- a permanent, user-authored "
                         "label that wins over any auto-generated one")
    sv.add_argument("--resolve-plan", action="store_true", dest="resolve_plan",
                    help="settle plan-step matches this save couldn't auto-confirm (n:m / "
                         "multi-step); runs standalone on a clean tree to resolve a prior save's "
                         "leftover ambiguity. Pass --confirm-hollow/--confirm-op to confirm a group.")
    sv.add_argument("--confirm-hollow", action="append", dest="confirm_hollow", default=[],
                    help="(with --resolve-plan) the plan step hollow(s) to confirm against --confirm-op")
    sv.add_argument("--confirm-op", action="append", dest="confirm_op", default=[])
    sv.add_argument("--no-color", action="store_true", help="plain text, no ANSI color")
    sv.set_defaults(func=_cmd_save)

    uv = subs.add_parser("undo", parents=[parent])
    uv.add_argument("--force", action="store_true",
                    help="drop work committed after the edit being undone (0.2c) -- undo normally "
                         "refuses when its snapshot restore would clobber an intervening raw commit")
    uv.set_defaults(func=_cmd_undo)


def _cmd_switch(args) -> int:
    return _switch(".", args.branch, args.as_json)


def _cmd_save(args) -> int:
    return _save(".", args.message, args.as_json, resolve_plan=args.resolve_plan,
                 confirm_hollow=args.confirm_hollow, confirm_op=args.confirm_op,
                 as_label=args.as_label, color=not args.no_color)


def _cmd_undo(args) -> int:
    return _undo(".", args.as_json, force=args.force)


def _switch(repo: str, branch: str, as_json: bool) -> int:
    """`sgt switch <branch>` (D3): materialize a named ideal -- an existing branch's committed
    tree. Mines the current ref first so nothing is lost (R9), moves HEAD to `branch` (git writes
    that branch's tree, which *is* `code(ideal)`), then mines the new ref so the op store reflects
    it. sgt owns the write path; a raw `git switch` is what D2 refuses in favor of this.

    The argument must name a *local branch*. It used to go straight to `git checkout`, so a commit
    sha or tag detached HEAD with no warning -- and a `sgt save` on a detached HEAD writes a commit
    that belongs to no branch and disappears from every ideal the moment the user switches back.
    Refusing here (rather than materializing a state sgt cannot keep) is the only honest answer:
    sgt's history is append-only, so "go look at an old state" is a read, not a checkout."""
    from sgt.core.lens import DirtyWorkingTreeError, get
    from sgt.store.gitbind import GitBinding, GitError

    gb = GitBinding(repo)
    if not gb.local_branch_exists(branch):
        known = gb.local_branches()
        resolved = gb.rev_parse(branch)
        what = "a commit or tag" if resolved else "nothing in this repo"
        listing = ", ".join(known[:8]) + ("…" if len(known) > 8 else "") if known else "(none)"
        return _fail_json(
            f"`{branch}` is not a local branch -- it resolves to {what}. `sgt switch` moves HEAD, "
            f"and switching to anything but a branch detaches it, where a later `sgt save` commits "
            f"onto no branch. Branches here: {listing}. To *look at* an older state instead, use "
            f"`sgt log --at <commit>` or `sgt diff <ref> HEAD`; to detach deliberately, "
            f"`sgt git checkout --force {branch}`.",
            as_json,
        )
    try:
        get(repo)  # absorb current reality before leaving this ref (R9)
        gb.checkout_branch(branch)  # move HEAD + materialize the branch's committed tree
        ideal = get(repo)  # reconcile the now-current ref's ideal
    except (GitError, DirtyWorkingTreeError, ValueError) as e:
        return _fail_json(str(e), as_json)

    n = len(ideal.op_ids)
    if as_json:
        return _emit_json({"ok": True, "branch": branch, "ops": n})
    print(f"✓ switch {branch}: {n} op(s) in the ideal")
    return 0


def _snapshot_cascade_tables(repo: str) -> dict[str, bytes | None]:
    """Raw bytes of the committed `.sgt` tables the save cascade (`ledger.assign_at_save`) writes --
    pins/authored/tree -- or `None` where a file is absent. Captured before the cascade so a `put`
    that refuses afterward (or a cascade that errors mid-write) can restore them: a save that does
    not finish must not leave `.sgt` dirty either (F1, on the error path)."""
    from sgt import state
    return {name: (state.path(repo, name).read_bytes() if state.path(repo, name).is_file() else None)
            for name in ("pins", "authored_features", "tree")}


def _restore_cascade_tables(repo: str, snapshot: dict[str, bytes | None]) -> None:
    """Undo the cascade's writes to their pre-cascade bytes, deleting any file the cascade created,
    so an aborted save leaves no committed-table dirt behind."""
    from sgt import state
    for name, raw in snapshot.items():
        p = state.path(repo, name)
        if raw is None:
            p.unlink(missing_ok=True)
        else:
            p.write_bytes(raw)


def _save(repo: str, message: str | None, as_json: bool, *, resolve_plan: bool = False,
          confirm_hollow: list[str] = (), confirm_op: list[str] = (),
          as_label: str | None = None, color: bool = False) -> int:
    """`sgt save [-m]` (D3): the put-path sugar -- mine the working tree (R9), then materialize a
    witness commit for the resulting ideal and record it. "Nothing to save" is decided by the
    ideal, not git's dirty flag: with no uncommitted ops the mined ideal equals the recorded one
    (a `.sgt/` untracked in an unconfigured repo would otherwise always read as dirty).

    Plan-matching is folded in (U12/R10): after a save, every *unambiguous* single-step match
    between an active plan session's pending steps and the just-mined ops auto-confirms -- no
    separate `sgt checkpoint`. An n:m / multi-step match instead waits for `sgt save --resolve-plan`
    (which also runs standalone on a clean tree to settle a prior save's leftover ambiguity, and,
    given `--confirm-hollow`/`--confirm-op`, confirms one named group). Ops matching no active plan
    (the old `sgt drift`) surface in this output; the working-tree sense of "drift" keeps its name
    in `status`/`fsck --tree`."""
    from sgt.core.lens import (
        DirtyWorkingTreeError, current_ideal, get, merge_in_progress, put, record_ideal,
    )
    from sgt.store.gitbind import GitBinding, GitError

    # 0.9/F26: an in-progress git merge/cherry-pick/revert leaves conflict-marker bytes in the tree
    # and a *_HEAD pseudo-ref set. Refuse *before* mining (`get`) so those markers never become ops
    # and the paused operation is never finalized blind. (`_sync` also skips its dirty pass while one
    # is live, the shared safety net for revert/switch/reads; save additionally refuses, loudly.)
    gb = GitBinding(repo)
    verb = merge_in_progress(gb)
    if verb is not None:
        return _fail_json(
            f"in-progress git {verb} -- finish or abort the git {verb} first "
            f"(git {verb} --continue / git {verb} --abort); sgt save won't commit conflict markers",
            as_json,
        )

    ideal = get(repo)  # mine the working tree (R9)

    # `save --resolve-plan --confirm-hollow ... --confirm-op ...` settles one ambiguous group by
    # name -- exactly what the removed `checkpoint --confirm-*` did, now reached through `save`.
    if resolve_plan and (confirm_hollow or confirm_op):
        return _confirm_plan_match(repo, list(confirm_hollow), list(confirm_op), as_json)

    prev_ids = current_ideal(repo).op_ids
    nothing_new = ideal.op_ids == prev_ids
    saved, sha, n = False, None, len(ideal.op_ids)
    cascade = None
    if not nothing_new:
        # Fold the save-time ownership cascade in (U6/R1/R2): assign every genuinely-new symbol a
        # durable lane (assign pin + authored CRDT) and patch the persisted tree's `op_leaf` so the
        # new op is visible on the grid immediately. This runs *before* `put` so the introducing
        # witness is stamped as the cascade's *parent* commit -- the causal anchor D6
        # (`verbs._save_pins`) documents -- rather than this verb's own commit. (Phase 1.2 made the
        # F1 invariant structural: pins/authored/tree are now gitignored and travel on
        # `refs/sgt/state`, which `put` publishes at its boundary, so `commit_all`'s `git add -A` no
        # longer sees them at all -- they can't dirty the tree regardless of ordering.)
        # Guarded: a lane-assignment hiccup must never fail the save. Local import keeps the path light.
        from sgt.core.store import Store
        from sgt.lens import ledger
        pre = _snapshot_cascade_tables(repo)
        try:
            cascade = ledger.assign_at_save(repo, ideal, Store(repo).all_ops())
        except Exception:  # noqa: BLE001 -- a save must still succeed even if the cascade errors
            _restore_cascade_tables(repo, pre)  # never commit a half-applied cascade
            cascade = None
        try:
            sha = put(repo, ideal, message=message or "sgt save")
        except (DirtyWorkingTreeError, GitError, ValueError) as e:
            # `put` refused (an uncommitted conflict or out-of-scope drift): roll the cascade's
            # writes back so an aborted save leaves the `.sgt` tables exactly as it found them, not
            # dirty -- the same F1 invariant, on the error path.
            _restore_cascade_tables(repo, pre)
            return _fail_json(str(e), as_json)
        record_ideal(repo, ideal, sha)
        saved = True
        # Zero-burden intent capture (intent-ledger M1): the `-m` message is the user's own words
        # about what this work was -- harvested as a turn keyed by the witness commit sha (a key
        # `_atom_prompt` already joins by, reachable from the new ops' provenance), never a new
        # prompt we asked them to type. Only a user-supplied message counts; the "sgt save" default
        # placeholder is not intent. Guarded like the cascade: capture must never fail a save.
        if message:
            try:
                from sgt.intent.turns import record_turn
                record_turn(repo, key=sha, key_kind="sha", actor="human", channel="cli",
                            text=message)
            except Exception:  # noqa: BLE001
                pass
    elif not resolve_plan:
        msg = "nothing to save -- no uncommitted ops"
        if as_json:
            return _emit_json({"ok": True, "saved": False, "message": msg})
        print(f"✓ {msg}")
        return 0

    # The labeling moment (feedforward): the save is when the user encodes what this work *was*,
    # so show which feature(s) the new ops landed in -- by name -- and let `--as` name the
    # dominant one right here (an authored label, which permanently wins over generated ones).
    new_op_ids = ideal.op_ids - prev_ids
    features = _save_attribution(repo, new_op_ids, cascade) if saved else []
    renamed = _apply_save_label(repo, features, as_label) if (saved and as_label) else None
    if as_label and not saved:
        print('  --as names the feature a save lands in; nothing was saved, so nothing to name.')

    plan = _fold_plan_matches(repo)
    # Drain the residual (intent-ledger P1): a save is when new work lands, so this is the beat to
    # retire any stated-but-never-landed intent the save (or its plan confirmations) fulfilled, and
    # to age out stale ones -- so `sgt log --summary`'s "what needs attention" stays honest without
    # an open/done queue to groom. Guarded like every intent side-effect: it must never fail a save.
    if saved:
        try:
            from sgt.intent.rationale import auto_retire_open
            auto_retire_open(repo)
        except Exception:  # noqa: BLE001 -- draining the residual is subordinate to the save
            pass
    words = _echo_words(repo, message, plan) if saved else None
    why = _aligned_why(repo, new_op_ids, words) if saved else None  # pure read; the ledger already
    # Open forks are non-blocking (save never refuses on them), but a divergence you must resolve
    # shouldn't hide -- surface it loudly at the end of every save (⋔ banner + per-symbol remedy).
    from sgt.api import forks_view
    open_forks = forks_view(repo)["forks"]
    return _render_save(as_json, saved, sha, n, plan, resolve_plan, words=words,  # holds only ALIGN
                        message=message, features=features, renamed=renamed, why=why,  # + confirmed
                        open_forks=open_forks, color=color)


def _fold_plan_matches(repo: str) -> dict | None:
    """After a save (U12/R10), auto-confirm every unambiguous single-step plan match -- one pending
    step matched to its op(s) -- and leave n:m / multi-step groups for `save --resolve-plan`. Then
    the housekeeping beat: auto-close any stalled plan whose remaining work clearly already landed
    (file-covered but never name-matched -- see `sgt.loop.plan.sweep_built_sessions`), so
    built-but-unmatched plans don't accrete on the "needs you" surface.
    Returns the fold summary, or `None` when no active plan session produced any match/drift and
    nothing was auto-closed (the common case), so `save`'s output and JSON are byte-unchanged then."""
    from sgt.loop import plan as plan_mod
    from sgt.loop.match import compute_checkpoint, confirm_match

    result = compute_checkpoint(repo)
    auto, ambiguous = [], []
    for g in result.matches:
        entry = {"session_id": g.session_id, "hollow_ids": list(g.hollow_ids), "op_ids": list(g.op_ids)}
        if len(g.hollow_ids) == 1:  # one pending step, unambiguous which -> confirm it now
            confirm_match(repo, g.session_id, list(g.hollow_ids), list(g.op_ids))
            auto.append(entry)
        else:  # multiple steps tangled in one op cluster -> needs `save --resolve-plan`
            ambiguous.append(entry)
    # After confirm, so coverage reads the leftovers -- but never close a session this very save is
    # about to tell the user to resolve. Doing so printed "run `sgt save --resolve-plan`" and
    # deleted the hollow that command needs, so the advertised next step answered "not a known
    # pending hollow id".
    auto_closed = plan_mod.sweep_built_sessions(
        repo, exclude=frozenset(e["session_id"] for e in ambiguous))
    if not result.matches and not result.drift_op_ids and not auto_closed:
        return None
    return {"auto_confirmed": auto, "ambiguous": ambiguous,
            "drift_op_ids": list(result.drift_op_ids), "auto_closed": auto_closed}


def _resolve_prefix(ref: str, known: set[str], kind: str) -> tuple[str | None, str]:
    """Resolve `ref` to a full id in `known` -- exact, else unique prefix -- mirroring
    `rewrite._resolve_op` for ids that aren't ops (a pending hollow). The `--resolve-plan` output
    prints truncated ids, so a user pasting one back must resolve, not be rejected for not being a
    verbatim full id (and a silently-wrong key must never reach `plan_matches.json`)."""
    if ref in known:
        return ref, ""
    matches = sorted(k for k in known if k.startswith(ref))
    if len(matches) == 1:
        return matches[0], ""
    if matches:
        return None, f"ambiguous {kind} prefix {ref!r}: {[m[:12] for m in matches[:5]]}"
    return None, f"{ref!r} is not a known pending {kind} id"


def _confirm_plan_match(repo: str, hollow_refs: list[str], op_refs: list[str], as_json: bool) -> int:
    """`save --resolve-plan --confirm-hollow ... --confirm-op ...`: confirm one named step<->op
    group, the explicit path the removed `checkpoint --confirm-*` verb owned (`confirm_match` is
    unchanged). Both ref kinds resolve by exact-or-unique-prefix so the (truncated) ids the
    `--resolve-plan` preview prints can be pasted straight back -- an op ref *must* resolve to a
    full canonical id, or `plan_matches.json` would key the match under the prefix and the real op
    would resurface as drift on the next checkpoint."""
    from pathlib import Path

    from sgt.core.rewrite import _resolve_op
    from sgt.core.store import Store
    from sgt.loop import plan as plan_mod
    from sgt.loop.match import confirm_match

    sessions = plan_mod.active_sessions(repo)
    known_hollows = {s["hollow_id"] for rec in sessions.values() for s in rec["steps"]}
    hollow_ids: list[str] = []
    for ref in hollow_refs:
        hid, err = _resolve_prefix(ref, known_hollows, "hollow")
        if hid is None:
            return _fail_json(err, as_json)
        hollow_ids.append(hid)

    ops = Store(repo).all_ops()
    op_ids: list[str] = []
    for ref in op_refs:
        oid, err = _resolve_op(Path(repo), ops, ref)
        if oid is None:
            return _fail_json(err, as_json)
        op_ids.append(oid)

    session_id = next(
        (sid for sid, rec in sessions.items() if any(s["hollow_id"] in hollow_ids for s in rec["steps"])),
        None,
    )
    if session_id is None:
        return _fail_json(f"no active session owns hollow(s) {hollow_ids}", as_json)
    confirm_match(repo, session_id, hollow_ids, op_ids)
    if as_json:
        return _emit_json({"ok": True, "session_id": session_id, "hollow_ids": hollow_ids, "op_ids": op_ids})
    print(f"✓ confirmed {len(hollow_ids)} hollow(s) matched to {len(op_ids)} op(s) in session {session_id}")
    return 0


def _save_attribution(repo: str, new_op_ids: frozenset, cascade: dict | None) -> list[dict]:
    """Which feature(s) this save's new ops landed in -- the label feedforward. One row per
    touched feature: its id, current label, a typeable handle, the (real) symbols this save
    touched in it, and whether the save minted the lane (`new` -- still unnamed). Empty when no
    tree has been built yet (a brand-new repo has nothing to attribute against)."""
    from sgt.core import opindex
    from sgt.core.op import _symbol_kind
    from sgt.lens.tree import load as load_tree

    tree_result = load_tree(repo)
    if not tree_result or not new_op_ids:
        return []
    op_leaf = tree_result.get("op_leaf", {})
    nodes = tree_result.get("nodes", {})
    by_id = {op.id: op for op in opindex.index_ops(repo)}
    new_lanes = set((cascade or {}).get("new_lanes", []))

    rows: dict[str, dict] = {}
    for oid in new_op_ids:
        leaf = op_leaf.get(oid)
        if leaf is None:
            continue
        row = rows.setdefault(leaf, {"feature_id": leaf, "symbols": set(), "edits": 0})
        row["edits"] += 1
        op = by_id.get(oid)
        if op is not None:
            row["symbols"].update(
                s for s in op.footprint
                if _symbol_kind(s) in ("entity", "nested", "whole_file")
            )
    out = []
    for leaf, row in rows.items():
        nd = nodes.get(leaf, {})
        out.append({
            "feature_id": leaf,
            "label": nd.get("label", leaf),
            "handle": leaf[2:10] if leaf.startswith("f-") else leaf[:11],
            "symbols": sorted(row["symbols"]),
            "edits": row["edits"],
            "new": leaf in new_lanes,
        })
    out.sort(key=lambda r: (-r["edits"], r["feature_id"]))
    return out


def _apply_save_label(repo: str, features: list[dict], label: str) -> dict:
    """`sgt save --as "<label>"`: name the save's feature at the moment of encoding. A lane this
    save minted wins (naming a brand-new thing); otherwise the save's dominant feature. Routed
    through the same plan/apply rename every `sgt feature rename` uses (authored LWW register,
    permanent)."""
    from sgt.lens import verbs as lens_verbs

    target = next((f for f in features if f["new"]), features[0] if features else None)
    if target is None:
        return {"ok": False,
                "message": 'no feature attribution yet -- run `sgt log --refresh`, then '
                           '`sgt feature rename <handle> "..."`'}
    pv = lens_verbs.plan_rename(repo, target["feature_id"], label)
    if not pv.ok:
        return {"ok": False, "message": pv.message}
    lens_verbs.apply_rename(repo, pv)
    target["label"] = label  # reflect the new name in this very output
    return {"ok": True, "feature_id": target["feature_id"], "label": label}


def _echo_words(repo: str, message: str | None, plan: dict | None) -> str | None:
    """The words captured for THIS save, from rung-0 (key-contained) sources only -- never a guessed
    nearest turn. Priority: the `-m` message the user just typed, else the stated intent of any plan
    step this save auto-confirmed (harvested at plan intake, joined into `plan_matches` by
    `confirm_match`). `None` when neither exists, so the caller renders an explicit "no words
    captured" rather than inventing one -- the save echo is the trust loop and must never bluff
    (a confidently-wrong words line at the highest-frequency surface teaches distrust faster than
    silence). Chat-session words are deliberately absent here: they arrive with the P2 alignment
    rung, not this pure-projection stage."""
    if message and message.strip():
        return message.strip()
    if plan:
        from sgt.loop.match import recorded_matches
        matches = recorded_matches(repo)
        seen: list[str] = []
        for e in plan.get("auto_confirmed", []):
            for oid in e["op_ids"]:
                intent = (matches.get(oid) or {}).get("intent")
                if intent and intent not in seen:
                    seen.append(intent)
        if seen:
            return "; ".join(seen)
    return None


def _aligned_why(repo: str, new_op_ids: frozenset, words: str | None) -> str | None:
    """The aligned *why* for the ops this save just landed: the reason texts the intent ledger
    already holds for them -- an aligner ALIGN (high-posterior), a human-confirmed review, or a
    plan-step intent. Distinct from `_echo_words` ("what you said" for this save): this is the
    ledger's answer to "why does this code exist", surfaced at the save beat.

    Honest by construction: the ledger only ever holds ALIGN + human-confirmed records (REVIEW-region
    guesses live in the separate `intent_review` queue and never reach here), so there is no pending
    guess to leak. Superseded and open (never-landed) records are skipped, and any reason identical
    to the words already echoed is dropped so the line never just repeats itself. `None` when the
    ledger has nothing landed for these ops -- the common case at save time, since the aligner runs
    as its own pass, not on the save beat."""
    if not new_op_ids:
        return None
    from sgt.intent import rationale

    recs = list(rationale.load_rationale(repo).values())
    dead = rationale._superseded_ids(recs)
    already = words.strip().lower() if words else None
    reasons: list[str] = []
    for r in recs:
        if r["id"] in dead or r.get("open") or not r.get("reason"):
            continue
        if not any(s["op"] in new_op_ids for s in r.get("subject", [])):
            continue
        reason = r["reason"].strip()
        if reason.lower() == already or reason in reasons:
            continue
        reasons.append(reason)
    return "; ".join(reasons) if reasons else None


def _render_save(as_json: bool, saved: bool, sha: str | None, n: int,
                 plan: dict | None, resolve_plan: bool, *, message: str | None = None,
                 features: list[dict] = (), renamed: dict | None = None,
                 words: str | None = None, why: str | None = None,
                 open_forks: list[dict] = (), color: bool = False) -> int:
    if as_json:
        out: dict = {"ok": True, "saved": saved, "open_fork_count": len(open_forks)}
        if saved:
            out["commit"], out["ops"] = sha, n
        if words:  # the captured words, structured for the editor/VSCode surface
            out["words"] = words
        if why:  # the ledger's aligned reason for these ops (distinct from the captured words)
            out["why"] = why
        if features:
            out["features"] = list(features)
        if renamed is not None:
            out["renamed"] = renamed
        if plan is not None:
            out["plan"] = plan
        return _emit_json(out)

    if saved:
        from sgt.tui.color import color_for
        from sgt.tui.graph import _dim, _paint
        # Feature-colored sha, matching the rail's own coding (the dominant feature = most-touched).
        dom = features[0]["feature_id"] if features else ""
        sha_s = _paint(color_for(dom), sha[:7], color=color) if (color and dom) else sha[:7]
        title = f' "{message}"' if message else ""
        print(f"✓ save {sha_s}{title}")
        # Capture legibility (intent-ledger P1): when the header didn't already echo an `-m`
        # message, show what capture actually holds for this save -- the plan step's words, or an
        # explicit empty state. Never the temporally-nearest turn: the echo shows only words truly
        # keyed to this save, so it cannot misattribute.
        if not message:
            import textwrap
            print(f'  · {textwrap.shorten(words, width=72, placeholder="…")}' if words
                  else "  · no words captured")
        if why:
            import textwrap
            print(f'  · why (aligned): {textwrap.shorten(why, width=60, placeholder="…")}')
        # Post-save graph (Issue 4): the save node branching into the feature lane(s) its new ops
        # landed in, drawn in the rail's own vocabulary (feature-colored ● live / ○ freshly minted)
        # so `sgt save` and `sgt log` read the same -- not a stale full-map rail (a save doesn't
        # rebuild the map), but the accurate save-time attribution rendered as a compact tree.
        for i, f in enumerate(features):
            conn = "└─" if i == len(features) - 1 else "├─"
            hexc = color_for(f["feature_id"])
            glyph = _paint(hexc, "○" if f["new"] else "●", color=color)
            syms = ", ".join(f["symbols"][:3]) + (f" +{len(f['symbols']) - 3} more"
                                                  if len(f["symbols"]) > 3 else "")
            if f["new"]:
                name = _paint(hexc, "new feature", color=color)
                tail = _dim(f'({f["handle"]}) — unnamed; name it: '
                            f'sgt feature rename {f["handle"]} "<label>"', color=color)
            else:
                name = _paint(hexc, f["label"], color=color)
                tail = _dim(f'({f["handle"]})', color=color)
            print(f"  {conn} {glyph} {name} {tail}  {syms}")
        if renamed is not None:
            if renamed["ok"]:
                print(f'  ✓ named "{renamed["label"]}"')
            else:
                print(f"  ⚠ --as failed: {renamed['message']}")
        if len(features) >= 3:
            print(f"  ⚠ one save touched {len(features)} features — deliberate? "
                  f"`sgt log --map` shows them; `sgt feature regroup move` re-files work")
        # Un-save discoverability (Issue 2): make reversing the save one obvious command. `sgt undo`
        # pops the tail event of the op-log, which right now is this save's ideal edit -- so it drops
        # exactly this save, returning its ops to pending (they stay on the tree, re-minable).
        print(_dim("  ⤺ reverse this save:  sgt undo", color=color))
    if plan is not None:
        for e in plan["auto_confirmed"]:
            steps = ", ".join(h[:12] for h in e["hollow_ids"])
            print(f"  ✓ plan step {steps} fulfilled by {len(e['op_ids'])} op(s)")
        for e in plan["ambiguous"]:  # show the group so the user can name it to --resolve-plan
            print(f"  ⚠ ambiguous: {len(e['hollow_ids'])} step(s) <-> {len(e['op_ids'])} op(s) "
                  "-- run `sgt save --resolve-plan`")
            if resolve_plan:
                print(f"      hollow: {', '.join(h[:12] for h in e['hollow_ids'])}")
                print(f"      op:     {', '.join(o[:12] for o in e['op_ids'])}")
        if plan["drift_op_ids"]:
            print(f"  drift: {', '.join(o[:12] for o in plan['drift_op_ids'])}")
        for sid in plan.get("auto_closed", []):
            print(f"  ✓ auto-closed built plan {sid[:12]} (work landed under a different name "
                  "than predicted)")
    elif resolve_plan and not saved:
        print("✓ no plan matches to resolve")
    if open_forks:
        from sgt.tui.graph import _state_banner
        for line in _state_banner({"forks": open_forks}, color=color):
            print(line)
    return 0


def _undo(repo: str, as_json: bool, *, force: bool = False) -> int:
    """`sgt undo` (D3, R7): invert the last mutating operation. Walks the *unified* operation log
    (U8/KTD6) reverse-chronologically -- popping the tail event and applying its inverse, whatever
    its kind: an ideal edit re-materializes its prior ideal, a feature reorg restores its snapshot,
    a shared-out `land`/`propose` is refused. History is append-only, so an undo is a forward edit,
    never a ref rewind. `force` overrides the F3 guard that refuses an undo whose snapshot restore
    would clobber work committed after the edit (0.2c)."""
    from sgt.core import oplog
    from sgt.core.lens import DirtyWorkingTreeError
    from sgt.store.gitbind import GitError

    # Show what it will do before doing it. `undo` is what a developer reaches for when something
    # has gone wrong, and it used to apply on contact -- so the one command whose whole job is
    # recovering from a surprise was itself a surprise. The gate is tty-only and matches `revert`'s
    # (`--json` and non-interactive callers apply immediately, the machine contract).
    import sys as _sys

    if not as_json and _sys.stdin.isatty() and _sys.stdout.isatty():
        pv = oplog.preview(repo, force=force)
        if pv["kind"] is not None:
            print(f"undo: {pv['message']}")
            if pv["restored"]:
                print(f"  brings back {len(pv['restored'])} edit(s)")
            if pv["dropped"]:
                print(f"  drops {len(pv['dropped'])} edit(s) made since")
            if pv["symbols"]:
                print(f"  touches {', '.join(pv['symbols'][:6])}"
                      + (" …" if len(pv["symbols"]) > 6 else ""))
            if not pv["ok"]:
                return _fail(pv["message"])
            try:
                reply = input("apply this undo? [y/N] ").strip().lower()
            except EOFError:
                reply = ""
            if reply not in ("y", "yes"):
                print("  aborted — nothing undone.")
                return 1

    try:
        outcome = oplog.undo(repo, force=force)
    except (DirtyWorkingTreeError, GitError, ValueError) as e:
        return _fail_json(str(e), as_json)

    if outcome.status == "empty":
        # Byte-identical to the pre-U8 message (a golden CLI snapshot pins it).
        msg = "nothing to undo -- no recorded ideal edits"
        if as_json:
            return _emit_json({"ok": True, "undone": False, "message": msg})
        print(f"✓ {msg}")
        return 0

    if outcome.status == "refused":
        return _fail_json(outcome.message, as_json)

    if outcome.status == "ideal_edit":
        result = outcome.ideal
        if as_json:
            return _emit_json({
                "ok": True, "undone": True, "commit": result.witness_sha,
                "restored_ops": len(result.ideal.op_ids),
                "removed": sorted(result.removed), "added": sorted(result.added),
            })
        parts = []
        if result.removed:
            parts.append(f"{len(result.removed)} op(s) back to pending")
        if result.added:
            parts.append(f"{len(result.added)} op(s) restored")
        detail = f" — {', '.join(parts)}" if parts else ""
        print(f"✓ undo {result.witness_sha[:7]}: restored the prior ideal{detail}")
        return 0

    # A metadata-snapshot kind (feature reorg / declared edge).
    if as_json:
        return _emit_json({"ok": True, "undone": True, "kind": outcome.kind, "message": outcome.message})
    print(f"✓ undo: {outcome.message}")
    return 0
