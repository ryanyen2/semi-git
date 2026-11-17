"""`revert` and `restore`: resolve what the reference names, then ask the planner
(`ugh.core.planner`) what taking it out, or bringing it back, would take."""

from __future__ import annotations

import argparse

from ugh.select import resolve as select_resolve

from ._common import _emit_json, _fail_json


def _theme_id_for_label(repo: str, target: str) -> str | None:
    """The theme whose label is exactly `target`, case- and punctuation-insensitively; `None` if
    no theme or more than one matches.

    `ugh log` prints themes by label, so the label is what a person has in front of them
    and the id (`theme-4a6670e474a2`) is what they would have to copy. Without this, typing the
    printed name fell past the theme rung to the fuzzy one and matched a *different* object: on
    the study testbed `ugh revert "Event-Day Handling"` proposed the feature "Event Day Tracking",
    which is a neighbouring two-commit feature rather than the five-feature theme. Suggesting the
    wrong grouping under the right name is worse than not resolving, because the preview that
    follows is about something else and reads as if it were about what was asked for.

    Matching folds case and strips non-alphanumerics, so the hyphen in "Event-Day Handling" is not
    the difference between resolving and not. Ambiguity declines to guess, like every other rung."""
    from ugh import state

    try:
        themes = state.load_json(repo, "intent_themes", default={}) or {}
    except Exception:  # noqa: BLE001 -- a read feeding resolution; never fail the verb
        return None
    squash = lambda s: "".join(ch for ch in str(s).casefold() if ch.isalnum())  # noqa: E731
    needle = squash(target)
    if not needle:
        return None
    hits = [tid for tid, body in themes.items() if squash((body or {}).get("label", "")) == needle]
    return hits[0] if len(hits) == 1 else None


def _resolve_theme(repo: str, target: str) -> tuple[frozenset[str], str] | None:
    """A theme id -> the ops its member commits landed, plus a human label."""
    from ugh import state
    from ugh.intent import group as intent_group

    try:
        themes = state.load_json(repo, "intent_themes", default={}) or {}
        resolved = intent_group.resolve_group(target, themes, intent_group.atoms(repo))
    except Exception:  # noqa: BLE001 -- an unresolvable theme falls through to the other rungs
        return None
    if resolved is None:
        return None
    _kind, members = resolved
    op_ids = frozenset(oid for a in members for oid in getattr(a, "op_ids", ()) or ())
    if not op_ids:
        return None
    label = (themes.get(target) or {}).get("label") or target
    return op_ids, label


def _dirty_refusal(exc, as_json: bool) -> int:
    """Render a `DirtyWorkingTreeError` from a materializing verb as a clean refusal, not a raw
    traceback with a half-written `.sgt` (F4/F5). The guard's message already names the offending
    files; append the *actual*, executable remedy -- absorb the edits with `ugh save`, or commit /
    `git restore` them, then re-run. (`ugh` never overwrites uncommitted work; there is no `ugh put`
    verb, and `log --refresh` does not clear a dirty tree.)"""
    msg = str(exc)
    if "overwrite uncommitted changes" in msg:
        msg += (" -- record them with `ugh save`, or commit / `git restore` those files, then "
                "re-run (ugh won't overwrite uncommitted work)")
    return _fail_json(msg, as_json)


def register(subs, parent) -> None:
    r = subs.add_parser("revert", parents=[parent])
    # `--emit` (machine dry-run: VS Code/MCP) and `--yes` (non-tty apply) are hidden: on a tty the
    # preview and its `[y/N]` are the default confirm step.
    r.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--whole", action="store_true",
                   help="take out the removal set: the target and everything that stands on it")
    r.add_argument("--out", default=None, metavar="DIR",
                   help="with --emit: write the previewed tree onto DIR as raw bytes")
    r.add_argument("ref", nargs="*")
    r.set_defaults(func=_cmd_revert)

    s = subs.add_parser("restore", parents=[parent])
    s.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--out", default=None, metavar="DIR",
                   help="with --emit: write the previewed tree onto DIR as raw bytes")
    s.add_argument("ref", nargs="*")
    s.set_defaults(func=_cmd_restore)


def _cmd_revert(args) -> int:
    return _kernel_edit_verb(".", "revert", args.ref, args.emit, args.as_json, args.yes,
                             whole=args.whole, out=args.out)


def _cmd_restore(args) -> int:
    return _kernel_edit_verb(".", "restore", args.ref, args.emit, args.as_json, args.yes,
                             out=args.out)


def _restorable_on(repo: str, symbol: str) -> frozenset[str] | None:
    """For `restore file::name`: the out steps on that symbol."""
    from ugh.core import opindex, planner

    out = planner.load_out(repo)
    ops = [o for o in opindex.index_ops(repo) if o.id in out and symbol in o.footprint]
    return frozenset(o.id for o in ops) or None


def resolve_ref(repo: str, cmd: str, target: str) -> tuple[frozenset[str], str] | None:
    """The deterministic reading of `target`: a theme (id or label), a checkpoint, a feature, a
    save (commit sha), an op id or prefix, or a symbol. `(op ids, label)`, or None."""
    from ugh.core import lens, opindex
    from ugh.lens import verbs as lens_verbs

    theme = target if target.startswith("theme-") else _theme_id_for_label(repo, target)
    if theme is not None:
        themed = _resolve_theme(repo, theme)
        if themed is not None:
            return themed
    if select_resolve.is_checkpoint_shaped(target):
        from ugh.intent.segment import resolve_checkpoint

        resolved = resolve_checkpoint(repo, target)
        if resolved is not None:
            return frozenset(resolved[0]), target
    if cmd == "restore" and "::" in target:
        hit = _restorable_on(repo, target)
        if hit:
            return hit, target
    feature = lens_verbs.resolve_feature(repo, target)
    if select_resolve.is_handle_shaped(target) and feature is not None:
        return frozenset(feature[0]), feature[2]
    op_id, _err = select_resolve.resolve_target(lens.current_ideal(repo), opindex.index_ops(repo),
                                                target)
    if op_id is not None:
        return frozenset({op_id}), target
    if feature is not None:
        return frozenset(feature[0]), feature[2]
    save = select_resolve._save(repo, target) if select_resolve.is_handle_shaped(target) else None
    if save is not None:
        return frozenset(save.op_ids), save.sha[:7]
    if "::" not in target and not select_resolve.is_handle_shaped(target):
        from ugh.intent.segment import resolve_checkpoint_label

        by_label = resolve_checkpoint_label(repo, target)
        if by_label is not None:
            return frozenset(by_label[0]), target
    return None


def _kernel_edit_verb(
    repo: str, cmd: str, ref_tokens: list[str], emit: bool, as_json: bool, yes: bool = False,
    *, whole: bool = False, out: str | None = None,
) -> int:
    """revert/restore: resolve the reference to op ids, plan, then preview or apply. A reference
    the deterministic reading cannot place falls to the ledger of recorded reasons (revert only)
    and then to the natural-language rung."""
    from ugh.core.lens import get

    if not ref_tokens:
        print(f"usage: ugh {cmd} [--json] <ref>")
        return 2
    target = " ".join(ref_tokens)
    from ._common import busy
    with busy(f"working out what `{target}` covers…"):
        get(repo)  # mine-on-contact before planning/applying the edit (R9)

    resolved = resolve_ref(repo, cmd, target)
    if resolved is None:
        if select_resolve.is_checkpoint_shaped(target):
            from ugh.intent.segment import checkpoint_miss

            miss = checkpoint_miss(repo, target)
            if miss is not None:
                return _no_checkpoint_match(cmd, target, miss, as_json)
        if select_resolve.is_handle_shaped(target):
            return _no_feature_match(repo, cmd, target, as_json)
        if "::" in target:
            return _fail_json(f"{target!r} names no definition in the code" if cmd == "revert"
                              else f"nothing on {target!r} is out; nothing to restore", as_json)
        from ugh.intent.segment import checkpoint_label_candidates

        ambiguous = checkpoint_label_candidates(repo, target)
        if len(ambiguous) > 1:
            return _fail_json(
                f"{target!r} names {len(ambiguous)} checkpoints: " + ", ".join(ambiguous)
                + f". Say which -- e.g. `ugh {cmd} {ambiguous[0]}`.", as_json)
        resolved = _resolve_via_ledger(repo, cmd, target)
        if resolved is None:
            return _resolve_via_intent(repo, cmd, target, as_json, yes, whole)
    op_ids, label = resolved
    return _answer(repo, cmd, op_ids, label, emit, as_json, yes, whole=whole, out=out)


def _answer(repo: str, cmd: str, op_ids, label: str, emit: bool, as_json: bool, yes: bool,
            *, whole: bool = False, out: str | None = None) -> int:
    """Plan, then print the preview (`--emit`), ask, or apply."""
    import sys

    from ugh.core import planner
    from ugh.core.lens import DirtyWorkingTreeError

    p = planner.plan(repo, op_ids, verb=cmd, target=label, whole=whole)
    if emit:
        view = planner.to_json(p)
        if out is not None and p.ok:
            from ugh.api import verb_result_out_view

            view["out"] = verb_result_out_view(repo, p, out)
        if as_json:
            _emit_json(view)
        else:
            _print_plan(p)
        return 0 if p.ok else 1
    if not p.ok:
        return _fail_json(p.message, as_json)
    if not p.files:
        if as_json:
            _emit_json(planner.to_json(p))
        else:
            _print_plan(p)
        return 0
    if not as_json:
        _print_plan(p)
        if not yes:
            if not sys.stdin.isatty():
                print("\n  not applied — this was the preview. re-run with --yes to apply.")
                return 2
            try:
                reply = input(f"\napply this {cmd}? [y/N] ").strip().lower()
            except EOFError:
                reply = ""
            if reply not in ("y", "yes"):
                print("  skipped — nothing changed.")
                return 1
    try:
        done = planner.apply(repo, p)
    except DirtyWorkingTreeError as e:
        return _dirty_refusal(e, as_json)
    applied = bool(done.check != "not_run" and done.check["passed"] and done.files)
    if as_json:
        _emit_json(planner.to_json(done, applied=applied))
        return 0 if applied else 1
    if applied:
        print(f"  ✓ {cmd} applied — {len(done.undone)} step(s) undone, {len(done.held)} held, "
              f"{_judged(done)}. (`ugh undo` reverses this.)")
        if done.held != p.held:
            _print_held(done)
        return 0
    print(f"  ✗ nothing applied — {_judged(done)}; the tree is as it was.")
    _print_held(done)
    return 1


def _judged(p) -> str:
    check = p.check
    if check == "not_run":
        return "check not run"
    what = "the project's check" if check["judge"] == "check" else "the parser"
    return f"{what} {'passes' if check['passed'] else 'fails'}"


def _short(p, op_id: str) -> str:
    sha, subject = p.saves.get(op_id, (op_id[:7], ""))
    return f'{sha} "{subject}"' if subject else sha


def _print_held(p) -> None:
    for hd in p.held:
        st = hd["step"]
        holders = ", ".join(dict.fromkeys(_short(p, h) for h in hd["holders"]))
        print(f"    {st.symbol} ({_short(p, st.op)}) — {hd['reason'].replace('_', ' ')}"
              + (f": {holders}" if holders else ""))


def _print_plan(p) -> None:
    """The three parts, plain: what goes, what is held and why; the removal set; where the held
    part is and which saves must keep working."""
    print(f"  {p.verb} {p.target}")
    if p.message:
        print(f"  {p.message}")
    if not p.steps:
        return
    saves = sorted({_short(p, st.op) for st in p.undone})
    print(f"  {'goes' if p.verb == 'revert' else 'comes back'}: {len(p.undone)} step(s)"
          + (f" ({', '.join(saves)})" if saves else ""))
    if p.held:
        print(f"  held: {len(p.held)} step(s)")
        _print_held(p)
    if p.removal_set and p.removal_set["steps"] > len(p.undone):
        rows = ", ".join(f'{s["sha"]} ({s["steps"]})' for s in p.removal_set["saves"])
        print(f"  --whole takes {p.removal_set['steps']} step(s): {rows}")
    if p.held:
        symbols = sorted({hd["step"].symbol for hd in p.held})
        keep = sorted({_short(p, h) for hd in p.held for h in hd["holders"]})
        print(f"  by hand: {', '.join(symbols)}"
              + (f"; keep working: {', '.join(keep)}" if keep else ""))
    files = sorted(p.files)
    print(f"  files: {', '.join(files)}" if files else "  no file changes")
    print("  check: not run yet" if p.check == "not_run" else f"  check: {_judged(p)}")


def _no_checkpoint_match(
    cmd: str, target: str, miss: tuple[str, str, list[str]], as_json: bool,
) -> int:
    """A `<feature>@<n>`/`<feature>:<slug>` whose feature resolved and whose selector did not.
    Deterministic and instant, like `_no_feature_match`: name the feature, say how many checkpoints
    it actually has, and list them as commands that run. Always exit 2."""
    feat_part, label, seg_labels = miss
    n = len(seg_labels)
    message = (f"{label!r} has {n} checkpoint{'' if n == 1 else 's'}, "
               f"so {target!r} names none of them")
    refs = [(f"{feat_part}@{i}", lbl) for i, lbl in enumerate(seg_labels)]
    if as_json:
        import json

        print(json.dumps({"ok": False, "verb": cmd, "target": target, "message": message,
                          "candidates": [{"ref": r, "label": lbl} for r, lbl in refs]}, indent=2))
        return 2
    print(f"? [{cmd}] {message}; did you mean:")
    for ref, lbl in refs[:8]:
        print(f"  ugh {cmd} {ref}   {lbl}")
    if len(refs) > 8:
        print(f"  ... and {len(refs) - 8} more — `--json` lists all of them.")
    return 2


def _no_feature_match(repo: str, cmd: str, target: str, as_json: bool) -> int:
    """A handle-shaped target (`f-`/bare-hex) that resolved to no *unique* feature -- a typo, a stale
    id, or a too-short (ambiguous) prefix. Answer deterministically and instantly (no LLM): list the
    leaf features whose handle the prefix matches, or point at `ugh log` when none do. Always exit 2."""
    from ugh.lens.tree import load as load_tree

    nodes = (load_tree(repo) or {}).get("nodes", {})
    bare = target[2:] if target.startswith("f-") else target

    def body(nid: str) -> str:
        return nid[2:] if nid.startswith("f-") else nid

    hits = sorted(
        (nid for nid, nd in nodes.items() if not nd["children"] and body(nid).startswith(bare)),
        key=lambda nid: nodes[nid].get("label", nid),
    )
    if as_json:
        import json

        cands = [{"ref": body(nid)[:8], "label": nodes[nid].get("label", nid)} for nid in hits]
        # `message` carries the same explanation the human path prints. Every UI
        # consumer shows `view.message || <generic>`, so a refusal without one
        # surfaces as "Cannot revert X." with no reason -- a dead end in exactly
        # the surface that cannot fall through to a terminal's stdout.
        message = (
            f"{target!r} is an ambiguous handle; candidates listed"
            if hits
            else f"no feature matches handle {target!r} -- it may be stale; refresh the view"
        )
        print(json.dumps({"ok": False, "verb": cmd, "target": target, "message": message,
                          "candidates": cands}, indent=2))
        return 2
    if not hits:
        print(f"? [{cmd}] no feature matches handle {target!r} -- run `ugh log` to see the handles.")
        return 2
    print(f"? [{cmd}] {target!r} is an ambiguous handle; did you mean:")
    for nid in hits[:8]:
        print(f"  ugh {cmd} {body(nid)[:8]}   {nodes[nid].get('label', nid)}")
    return 2




def _resolve_via_ledger(repo: str, cmd: str, target: str) -> tuple[frozenset[str], str] | None:
    """The deterministic NL rung, tried *before* the LLM (plan U8): match the prose `target`
    against the intent ledger's captured `reason` texts and take the best-matching record's subject
    op-set -- so "drop the retry logic" resolves offline. Revert-only. None when nothing matches
    uniquely."""
    if cmd != "revert":
        return None
    from ugh.intent import rationale

    phrase = set(rationale._content_words(target))
    if not phrase:
        return None
    recs = list(rationale.load_rationale(repo).values())
    dead = rationale._superseded_ids(recs)
    scored: list[tuple[int, float, dict]] = []
    for r in recs:  # the live, landed reasons -- mirror recall's filter (skip superseded/open/reasonless)
        if r["id"] in dead or r.get("open") or not r.get("reason"):
            continue
        score = rationale.topic_overlap(phrase, rationale._content_words(r["reason"]))
        if score:
            scored.append((score, r["ts"], r))
    if not scored:
        return None
    scored.sort(key=lambda sr: (-sr[0], -sr[1]))  # strongest overlap, then most recent
    best = scored[0][0]
    if len([s for s in scored if s[0] == best]) > 1:
        return None  # a tie is genuinely ambiguous -- let the LLM rung disambiguate rather than guess
    op_ids = frozenset(s["op"] for s in scored[0][2].get("subject", []))
    return (op_ids, target) if op_ids else None


def _resolve_via_intent(repo: str, cmd: str, target: str, as_json: bool, yes: bool,
                        whole: bool = False) -> int:
    """The NL rung: an LLM (`ugh.intent.resolve.resolve_intent`) proposes candidate refs for
    `target`; each is resolved deterministically and planned, dropping any that would change
    nothing. Default UX is did-you-mean -- print the survivors and the exact re-invoke command,
    exit 2, apply nothing. `--yes` applies the top survivor. No key, no network, or no surviving
    candidate reports a clear message and exits 1."""
    from ugh.core import planner
    from ugh.intent.resolve import resolve_intent

    resolution = resolve_intent(repo, target, verb=cmd)
    if resolution is None:
        try:
            from ugh.config import load_env, resolve_api_key

            load_env(repo)
            keyed = bool(resolve_api_key(repo))
        except Exception:  # noqa: BLE001 -- any config failure reads as "no usable credential"
            keyed = False
        return _fail_json(
            f"nothing in this repository matches {target!r}. Names that work: a feature or "
            "checkpoint name exactly as a view prints it, `<feature>@<n>`, a commit sha, or "
            "`file.py::Symbol`."
            if keyed else
            f"could not resolve {target!r} to a ref; set OPENAI_API_KEY to enable "
            "natural-language targets",
            as_json,
        )
    if not resolution.candidates:
        return _fail_json(
            f"nothing in this codebase's tracked history plausibly matches {target!r}", as_json,
        )

    survivors = []
    seen: set[frozenset[str]] = set()
    for cand in resolution.candidates:
        resolved = resolve_ref(repo, cmd, cand.ref)
        if resolved is None or resolved[0] in seen:
            continue
        p = planner.plan(repo, resolved[0], verb=cmd, target=cand.ref, whole=whole)
        if not p.ok or not p.files:
            continue
        seen.add(resolved[0])
        survivors.append((cand, resolved, p))
    if not survivors:
        return _fail_json(f"no live candidate for {target!r} survived planning", as_json)

    if yes:
        _cand, (op_ids, label), _p = survivors[0]
        return _answer(repo, cmd, op_ids, label, False, as_json, True, whole=whole)

    candidates_view = [
        {"ref": cand.ref, "kind": cand.kind, "rationale": cand.rationale,
         "undone": len(p.undone), "held": len(p.held), "reinvoke": f"ugh {cmd} {cand.ref}"}
        for cand, _r, p in survivors
    ]
    if as_json:
        import json

        print(json.dumps({"ok": False, "verb": cmd, "target": target, "candidates": candidates_view}, indent=2))
        return 2

    print(f"? [{cmd}] {target!r} did not resolve; did you mean:")
    for i, c in enumerate(candidates_view, 1):
        print(f"  {i}. {c['ref']} ({c['kind']}) — {c['rationale']}")
        print(f"     would undo {c['undone']} step(s), hold {c['held']}")
        print(f"     re-invoke: {c['reinvoke']}")
    return 2
