"""`sgt intent` (plan U6/U7/U8): the intent-clustering overlay's CLI surface.

`sgt intent list [--json]` and `sgt intent show <theme-id|commit-sha> [--json]` render
`sgt.api.intent_view` -- every commit-keyed atom (rung 0/1, recomputed on read) and every
persisted, LLM-named theme (rung 2), each with its dependency-graph-backed tier and cross-feature
span. `sgt intent build [--json]` is the one command that runs the LLM theme pass and writes
`.sgt/intent/themes.json` (`sgt.intent.theme.build_themes`) -- kept out of the read verbs, exactly
as `sgt map` (the write) is distinct from `map_view` (the read).

`sgt intent revert <theme-id|commit-sha> [--subset <sha>...] [--emit] [--json]` (KTD6): resolves
the target to its deterministic atom union (`sgt.intent.group.resolve_group` -- never from the
LLM's own output) and runs the *exact same* `verbs.plan_revert_op_set` -> `_emit_verb_result` path
as every other revert -- same up-set removal, same fork refusal, same oracle gate. The LLM only
ever decided the theme's *default* membership; a wrong boundary is a mis-default visible in the
preview and adjustable with `--subset`, never a silent destructive edit.
"""

from __future__ import annotations

import argparse

from ._common import _emit_json, _fail_json

_USAGE = ("usage: sgt intent list [--json] | "
          "sgt intent show <feature@n | theme-id | commit-sha> [--json] | "
          "sgt intent build [--recut <feature>] [--json] | "
          "sgt intent relabel <feature@n> \"<intent>\" [--json] | "
          "sgt intent revert <theme-id|commit-sha> [--subset <sha>...] [--json] | "
          "sgt intent open [--json] | sgt intent done <id> [--json] | "
          "sgt intent review [confirm|reject <id>] [--json] | "
          "sgt intent align [--apply] [--json]\n"
          "  (rewind a single checkpoint with `sgt revert <feature>@<n>`)")


def register(subs, parent) -> None:
    p = subs.add_parser("intent", parents=[parent])
    p.add_argument("sub", nargs="?", metavar="list|show|build",
                   help="list — every feature's checkpoints; show <commit-sha> — what one "
                        "commit was for (a COMMIT, not a feature id); build — (re)cut "
                        "checkpoints from the current graph")
    p.add_argument("target", nargs="?", metavar="<commit|feature>",
                   help="the commit sha (`show`) or feature id/label (`list`, `build --recut`)")
    p.add_argument("rest", nargs="*")  # the new label words for `relabel`
    p.add_argument("--subset", nargs="*")
    p.add_argument("--recut", metavar="FEATURE",
                   help="with `build`: re-cut one whole feature's checkpoints from scratch "
                        "(id/prefix/label), instead of the default incremental tail re-cut")
    # Hidden but functional (see revert): the tty consequence pane is the default confirm step.
    p.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--apply", action="store_true",
                   help="with `align`: write the records instead of only counting them")
    p.set_defaults(func=_cmd_intent)


def _cmd_intent(args) -> int:
    return _intent(".", args.sub, args.target, args.rest, args.subset, args.emit, args.as_json,
                   args.yes, args.recut, args.apply)


def _intent(
    repo: str, sub: str | None, target: str | None, rest: list[str] | None,
    subset: list[str] | None, emit: bool, as_json: bool, yes: bool = False,
    recut: str | None = None, apply: bool = False,
) -> int:
    from sgt.core.lens import get

    if sub == "record":  # the hook entry point: capture only, no mining -- must stay fast
        return _record(repo, as_json)
    if sub == "activity":  # the PostToolUse hook entry point: append one tool event, no mining
        return _activity(repo, as_json)
    if sub not in ("list", "show", "build", "revert", "relabel", "open", "done", "edit", "review",
                   "align"):
        print(_USAGE)
        return 2
    get(repo)  # mine-on-contact so the overlay reflects current reality (R9)
    if sub == "align":
        return _align(repo, as_json, apply)
    if sub == "list":
        return _list(repo, as_json)
    if sub == "build":
        return _build(repo, as_json, recut)
    if sub == "open":  # the intent-ledger unfulfilled-intent surface (M1), not the overlay
        return _open(repo, as_json)
    if sub == "review":  # the alignment review queue: pending pairs, confirm/reject to decide
        return _review(repo, target, rest, as_json)
    if target is None:
        print(_USAGE)
        return 2
    if sub == "done":
        return _done(repo, target, as_json)
    if sub == "edit":
        return _edit(repo, target, " ".join(rest or []), as_json)
    if sub == "relabel":
        return _relabel(repo, target, " ".join(rest or []), as_json)
    if sub == "revert":
        return _revert(repo, target, subset, emit, as_json, yes)
    return _show(repo, target, as_json)


def _open(repo: str, as_json: bool) -> int:
    """`sgt intent open`: the unfulfilled intents -- plan steps that were stated but never landed
    (their sessions closed with the step still pending). Surfaced so work you meant to do but didn't
    resurfaces, rather than vanishing with its hollow op."""
    from sgt.intent.rationale import open_intents

    opens = open_intents(repo)
    if as_json:
        return _emit_json({"open": [
            {"id": r["id"], "reason": r["reason"], "predicted_fp": r["predicted_fp"], "ts": r["ts"]}
            for r in opens
        ]})
    if not opens:
        print("no open intents -- everything stated has landed (or nothing was captured)")
        return 0
    print(f"{len(opens)} open intent(s) -- stated but not yet landed:")
    for r in opens:
        print(f"  [{r['id'][:12]}] {r['reason'] or '(unknown)'}")
    print("\n  retire one with `sgt intent done <id>`")
    return 0


def _align(repo: str, as_json: bool, apply: bool) -> int:
    """`sgt intent align [--apply]`: run the alignment pipeline over captured conversation turns and
    the ops that landed, answering "which prompts produced which code".

    Dry run by default, deliberately. The pipeline is calibrated over a corpus, and on a young one
    it scores confidently on too little evidence -- an ALIGN record it gets wrong becomes a wrong
    answer from `sgt why` and from an agent's `recall()`, which is worse than no answer at all. So
    the module has sat unwired since it was written (`sgt.intent.align_session`). Making it *look
    at* a repo costs nothing and is the only way to find out whether this repo's corpus is mature
    enough, which is exactly the judgment its author left open. `--apply` is that judgment being
    made, by the person whose repo it is. Unconfident pairs go to `sgt intent review` either way,
    where they never leak into recall until confirmed."""
    from sgt.intent.align_session import align_session

    counts = align_session(repo, write=apply)
    if as_json:
        return _emit_json({"ok": True, "applied": apply, **counts})
    mode = "wrote" if apply else "would write"
    print(f"{counts['sessions']} chat session(s) · {counts['episodes']} episode(s) · "
          f"{counts['candidates']} candidate pair(s)")
    print(f"  {mode} {counts['aligned']} rationale record(s); "
          f"{counts['reviewed']} pair(s) held for review")
    if not apply:
        if counts["aligned"] or counts["reviewed"]:
            print("  (dry run — re-run with `--apply` to record them, "
                  "then read them back with `sgt why <sha>`)")
        else:
            print("  (nothing to align yet — the pipeline needs captured prompts and landed ops)")
    return 0


def _review(repo: str, action: str | None, rest: list[str] | None, as_json: bool) -> int:
    """`sgt intent review`: the alignment review queue -- (op, episode) pairs the aligner scored but
    could not confidently ALIGN, held for a human call rather than guessed into the ledger. No action
    lists the pending pile; `confirm <id>` promotes one into the ledger (human-endorsed), `reject
    <id>` drops it. Both decisions are tombstoned, so a later re-align never re-surfaces them."""
    from sgt.intent import review

    if action in ("confirm", "reject"):
        rid = (rest or [None])[0]
        if not rid:
            return _fail_json(f"usage: sgt intent review {action} <id>", as_json)
        # Accept the short 12-char id the list prints, resolving it to the full id.
        full = next((k for k in review.load_review(repo) if k.startswith(rid)), rid)
        if action == "confirm":
            promoted = review.confirm_review(repo, full)
            if promoted is None:
                return _fail_json(f"no pending review {rid!r} to confirm", as_json)
            if as_json:
                return _emit_json({"confirmed": full, "rationale": promoted})
            print(f"confirmed {rid} -> ledger rationale {promoted[:14]} (recorded as your intent)")
            return 0
        if not review.reject_review(repo, full):
            return _fail_json(f"no pending review {rid!r} to reject", as_json)
        if as_json:
            return _emit_json({"rejected": full})
        print(f"rejected {rid} -- dropped from the queue")
        return 0
    if action is not None:
        return _fail_json(f"unknown review action {action!r} (use confirm|reject <id>)", as_json)

    pending = review.pending_reviews(repo)
    if as_json:
        return _emit_json({"pending": [
            {"id": r["id"], "reason": r["reason"], "posterior": r["posterior"],
             "signals": [s["name"] for s in r["signals"]],
             "subject": [s["op"] for s in r["subject"]], "ts": r["ts"]}
            for r in pending
        ]})
    if not pending:
        print("no pending reviews -- the aligner is confident about everything it has seen")
        return 0
    print(f"{len(pending)} pair(s) awaiting review (aligner unsure -- your call):")
    for r in pending:
        gens = ", ".join(s["name"] for s in r["signals"]) or "(none)"
        print(f"  [{r['id'][:12]}] p={r['posterior']:.2f} via {gens}")
        print(f"      why: {r['reason'] or '(unknown)'}")
    print("\n  confirm one with `sgt intent review confirm <id>`, drop with `... reject <id>`")
    return 0


# What `UserPromptSubmit` delivers that is NOT the user's voice: the harness routes background-task
# notifications, injected reminders, and slash-command markup through the same hook, each wrapped in
# a leading tag. A turn store that records these as `actor="human"` poisons every surface that
# trusts it -- `sgt now` reports a task-notification blob as "working on", and any prompt->op
# alignment weighs machine chatter as the developer's own words (dogfood 2026-09-01: 137 of 294
# captured turns were `<task-notification>`s). Only a LEADING tag marks an injection; a prompt that
# merely mentions one mid-sentence is still human and is kept verbatim.
_INJECTED_WRAPPERS = ("<task-notification>", "<system-reminder>", "<command-name>",
                      "<local-command-stdout>", "<command-message>")


def _record(repo: str, as_json: bool) -> int:
    """`sgt intent record`: the zero-burden capture entry a Claude Code `UserPromptSubmit` hook
    pipes into. Reads the hook's JSON payload from stdin (`{"session_id": ..., "prompt": ...}`)
    and stores the user's prompt verbatim as a local chat-keyed turn. Silent and fast by design:
    it runs on every prompt, so no mining, no output on success, exit 0 even on empty input --
    a capture hiccup must never disturb the user's conversation."""
    import json as _json
    import sys
    from pathlib import Path

    try:
        # Only capture into a repo that already opted in (`sgt init`): the hook fires wherever
        # Claude Code runs, and materializing `.sgt/` into an arbitrary cwd is pollution, not
        # capture (testbed 2026-07-31: the hook minted `/tmp/.sgt` on a stray fire).
        if (Path(repo) / ".sgt").is_dir():
            payload = _json.loads(sys.stdin.read() or "{}")
            chat, text = payload.get("session_id"), (payload.get("prompt") or "").strip()
            if chat and text and not text.startswith(_INJECTED_WRAPPERS):
                from sgt.intent.turns import record_turn
                record_turn(repo, key=chat, key_kind="chat", actor="human", channel="hook", text=text)
    except Exception:  # noqa: BLE001 -- never break the hook chain
        pass
    if as_json:
        return _emit_json({"ok": True})
    return 0


def _activity(repo: str, as_json: bool) -> int:
    """`sgt intent activity`: the zero-burden entry a Claude Code `PostToolUse` hook pipes into on
    every Edit/Write. Reads the hook's JSON payload from stdin (`{"tool_name", "tool_input":
    {"file_path"}, "session_id"}`) and appends one row to the local activity feed. Like `_record`
    it is silent and fast -- no mining, exit 0 even on empty input -- so a capture hiccup never
    disturbs the agent's turn."""
    import json as _json
    import sys
    from pathlib import Path

    try:
        # Same opt-in guard as `_record`: only feed a repo that ran `sgt init`, never mint `.sgt/`
        # into whatever cwd the hook happened to fire in.
        if (Path(repo) / ".sgt").is_dir():
            payload = _json.loads(sys.stdin.read() or "{}")
            tool = payload.get("tool_name") or ""
            file = (payload.get("tool_input") or {}).get("file_path")
            # The hook fires with cwd = the session's repo, but the edited file can live anywhere
            # (dogfood 2026-09-01: a sibling checkout's edits landed in this repo's feed). An edit
            # outside the repo root is another repo's motion, not this one's -- skip it.
            if file is not None and not Path(file).resolve().is_relative_to(Path(repo).resolve()):
                tool = ""
            if tool:
                from sgt.intent.activity import record_activity
                record_activity(repo, tool=tool, file=file, session_id=payload.get("session_id"))
    except Exception:  # noqa: BLE001 -- never break the hook chain
        pass
    if as_json:
        return _emit_json({"ok": True})
    return 0


def _edit(repo: str, target: str, reason: str, as_json: bool) -> int:
    """`sgt intent edit <id> "<reason>"`: correct/confirm a recorded rationale in your own words.
    Optional -- capture never depends on it; this is the lever for when the inferred why is wrong."""
    from sgt.intent.rationale import edit_rationale

    rid = edit_rationale(repo, target, reason)
    if rid is None:
        return _fail_json(f"no unique rationale {target!r} (or empty reason)", as_json)
    if as_json:
        return _emit_json({"ok": True, "id": rid})
    print(f"✓ recorded your correction ({rid[:12]}, confirmed)")
    return 0


def _done(repo: str, target: str, as_json: bool) -> int:
    """`sgt intent done <id>`: retire an open intent by hand -- the escape hatch for one that was
    actually finished (differently than predicted, so no op ever matched it) or no longer wanted.
    `<id>` may be the short prefix `sgt intent open` prints."""
    from sgt.intent.rationale import open_intents, retire_open

    matches = [r for r in open_intents(repo) if r["id"] == target or r["id"].startswith(target)]
    if len(matches) != 1:
        which = "no" if not matches else "ambiguous"
        return _fail_json(f"{which} open intent {target!r} (see `sgt intent open`)", as_json)

    retire_open(repo, matches[0]["id"])
    if as_json:
        return _emit_json({"ok": True, "retired": matches[0]["id"]})
    print(f"✓ retired open intent {matches[0]['id'][:12]}")
    return 0


def _tier_badge(tier: str) -> str:
    return {"coupled": "●", "co-changed": "◐", "thematic": "○"}.get(tier, "?")


def _novelty_bar(novelty: float) -> str:
    """A one-glyph weight for a checkpoint: how much behavior it changed. A dim glyph flags a
    checkpoint that mostly tweaked existing code -- a low-value rewind target."""
    return "█" if novelty > 0.6 else ("▓" if novelty > 0.2 else "░")


def _list(repo: str, as_json: bool) -> int:
    """Lead with the feature-scoped checkpoints (the rewind units): each feature, then its
    chronological checkpoints indented beneath, each addressable as `<feature>@<n>`. Themes (the
    cross-feature rollup) are a compact footer -- superseded by checkpoints as the primary unit,
    kept for the cross-cutting "one PR touched five features" view."""
    from itertools import groupby

    from sgt.api import intent_view

    view = intent_view(repo)
    if as_json:
        return _emit_json(view)

    segments = view["segments"]
    if not segments:
        print("(no feature tree yet -- run `sgt log --refresh` first)")
    any_unnamed = False
    for feature_id, segs in groupby(segments, key=lambda s: s["feature_id"]):
        segs = list(segs)
        label = segs[0]["feature_label"]
        any_unnamed = any_unnamed or not any(s["source"] == "llm" for s in segs)
        print(f"● {label}  [{feature_id[:14]}]  {len(segs)} checkpoint(s)")
        for s in segs:
            print(f"    {_novelty_bar(s['novelty'])} [{s['seg_index']}] {s['intent']}  "
                  f"({s['feature_id'][:10]}@{s['seg_index']})")
    if any_unnamed:
        print("\n(some checkpoints are unnamed -- `sgt intent build` names them)")
    # Cross-feature themes: a compact secondary section (superseded by checkpoints as the primary
    # unit, but still the "one PR spanning several features" rollup, and the target of
    # `sgt intent revert <theme-id>`). Stale members are surfaced here, not silently dropped.
    if view["themes"]:
        print("\ncross-feature themes:")
        for t in view["themes"]:
            span = ", ".join(f[:10] for f in t["feature_span"]) or "(no feature)"
            print(f"  {_tier_badge(t['tier'])} {t['label']}  [{t['theme_id']}]  "
                  f"across {span}  ({t['tier']}, {t['source']})")
            if t["stale_shas"]:
                names = ", ".join(sha[:8] for sha in t["stale_shas"])
                print(f"    ⚠ stale: {len(t['stale_shas'])} member commit(s) no longer resolve ({names})")

    n_feat = len({s["feature_id"] for s in segments})
    print(f"\n{n_feat} feature(s), {len(segments)} checkpoint(s)"
          + (f"; {len(view['themes'])} cross-feature theme(s)" if view["themes"] else ""))
    return 0


def _print_context(pack: dict) -> None:
    """The checkpoint context pack's terminal render (weave P4): asks in conversation order, the
    recorded why (`_print_rationale`'s badges, same everywhere), what editing here disturbs, and
    the way back into the conversation. Every section renders only what was recorded -- an empty
    one prints nothing rather than a placeholder."""
    if pack["asked"]:
        print("  asked (in order):")
        for a in pack["asked"]:
            first = next((ln.strip() for ln in a["text"].splitlines() if ln.strip()), "")
            print(f'    - "{first[:100]}"  ({a["channel"]})')
    if pack["why"]:
        print("  why (recorded):")
        for r in pack["why"]:
            badge = "confirmed" if r["confirmed"] else "inferred"
            ev = f" [{r['evidence']} turn(s)]" if r["evidence"] else ""
            print(f"    - {r['reason']}  ({r['actor']}, {badge}){ev}")
    if pack["dependent_op_ids"]:
        print(f"  since then: {len(pack['dependent_op_ids'])} op(s) built on this checkpoint -- "
              f"an edit from here disturbs them (preview: sgt revert {pack['checkpoint']} --emit)")
    for h in pack["resume"]:
        print(f"  resume: {h['command']}")


def _show(repo: str, target: str, as_json: bool) -> int:
    from sgt.api import intent_view

    view = intent_view(repo)

    # A `<feature>@<n>` checkpoint takes precedence -- it's the primary addressable unit.
    if "@" in target:
        feat_part, _, idx_part = target.rpartition("@")
        seg = next(
            (s for s in view["segments"]
             if str(s["seg_index"]) == idx_part
             and (s["feature_id"].startswith(feat_part)
                  or s["feature_label"].strip().lower() == feat_part.strip().lower())),
            None,
        )
        if seg is not None:
            # The context pack (weave P4): everything needed to go back to this chapter and keep
            # working from it -- the asks, the recorded why, the blast radius, the way back into
            # the conversation. One extra read; the pack resolves through the same segments_for
            # cut this listing came from, so the two cannot describe different chapters.
            from sgt.api import checkpoint_context
            pack = checkpoint_context(repo, seg["checkpoint"])
            if as_json:
                return _emit_json({"kind": "checkpoint", **seg,
                                   "context": pack if pack.get("ok") else None})
            print(f"{seg['feature_label']} · checkpoint {seg['seg_index']}  "
                  f"[{seg['feature_id'][:10]}@{seg['seg_index']}]  ({seg['tier']}, {seg['source']})")
            print(f"  intent: {seg['intent']}")
            print(f"  {seg['rationale']}")
            print(f"  {seg['op_count']} op(s), commits {seg['first_index']}-{seg['last_index']}, "
                  f"novelty {seg['novelty']}")
            if pack.get("ok"):
                _print_context(pack)
            print(f"  rewind: sgt revert {seg['feature_id'][:10]}@{seg['seg_index']}")
            return 0

    theme = next((t for t in view["themes"] if t["theme_id"] == target), None)
    matching_atoms = [a for a in view["atoms"] if a["commit_sha"].startswith(target)]

    if theme is not None:
        result = {"kind": "theme", **theme, "atoms": [
            a for a in view["atoms"] if a["commit_sha"] in theme["atom_shas"]
        ]}
    elif matching_atoms:
        result = {"kind": "atom", **matching_atoms[0]}
    else:
        return _fail_json(f"no theme or commit {target!r} found in the intent overlay", as_json)

    if as_json:
        return _emit_json(result)
    if result["kind"] == "theme":
        print(f"{result['label']}  [{result['theme_id']}]  ({result['tier']}, {result['source']})")
        print(f"  {result['rationale']}")
        print(f"  feature span: {', '.join(result['feature_span']) or '(none)'}")
        if result["stale_shas"]:
            names = ", ".join(sha[:8] for sha in result["stale_shas"])
            print(f"  ⚠ stale: {len(result['stale_shas'])} member commit(s) no longer resolve ({names})")
        for a in result["atoms"]:
            print(f"    {a['commit_sha'][:12]}  {a['subject']}  ({len(a['op_ids'])} op(s))")
    else:
        print(f"{result['commit_sha'][:12]}  {result['subject']}  ({result['tier']})")
        print(f"  feature span: {', '.join(result['feature_span']) or '(none)'}")
        print(f"  {len(result['op_ids'])} op(s)")
        if result["prompt"]:
            # Multi-line prompts (plan intake text) keep the block's indentation.
            print(f"  prompt: {result['prompt']}".replace("\n", "\n          "))
        for reason in result.get("rationale", []):
            print(f"  why: {reason}")
        if result.get("session_ids"):
            print(f"  session: {', '.join(result['session_ids'])}")
        for sid in result.get("claude_session_ids", []):
            print(f"  chat: claude --resume {sid}")
    return 0


def _relabel(repo: str, target: str, label: str, as_json: bool) -> int:
    """`sgt intent relabel <feature@n> "<new intent>"`: override a checkpoint's label in the
    user's words. Written to the committed `intent_segment_pins` layer keyed by the checkpoint's
    first commit sha, so the edit survives `sgt intent build` (which only rewrites the boundary/
    LLM-label layer). This is the "editing an intent = editing a checkpoint" path -- rare, but the
    one deliberate lever a user has over the overlay."""
    from sgt import state
    from sgt.api import intent_view

    if not label.strip():
        return _fail_json("relabel needs a non-empty label: sgt intent relabel <feature@n> \"<intent>\"", as_json)
    if "@" not in target:
        return _fail_json(f"{target!r} is not a checkpoint ref (expected <feature>@<n>)", as_json)

    feat_part, _, idx_part = target.rpartition("@")
    segments = intent_view(repo)["segments"]
    seg = next(
        (s for s in segments
         if str(s["seg_index"]) == idx_part
         and (s["feature_id"].startswith(feat_part)
              or s["feature_label"].strip().lower() == feat_part.strip().lower())),
        None,
    )
    if seg is None:
        return _fail_json(f"no checkpoint {target!r} found (see `sgt intent list`)", as_json)
    if not seg["commit_shas"]:
        return _fail_json(f"checkpoint {target!r} has no commit to pin the label to", as_json)

    pins = state.load_json(repo, "intent_segment_pins", default={})
    first_sha = seg["commit_shas"][0]
    pins.setdefault(seg["feature_id"], {})[first_sha] = label.strip()[:60]
    state.save_json_if_changed(repo, "intent_segment_pins", pins)

    if as_json:
        return _emit_json({"ok": True, "checkpoint": seg["checkpoint"], "label": label.strip()[:60]})
    print(f"✓ relabeled {seg['feature_id'][:10]}@{seg['seg_index']} → {label.strip()[:60]!r}")
    return 0


def _build(repo: str, as_json: bool, recut: str | None = None) -> int:
    """Run the LLM passes that name the overlay: feature-scoped checkpoints (`build_segments`, the
    primary unit) and the cross-feature themes (`build_themes`). Both are LLM-labeled with a
    deterministic offline fallback, content-hash cached, and rebuilt on demand -- kept out of the
    read verbs exactly as `sgt map` (the write) is distinct from `map_view` (the read). `recut`
    (`--recut <feature>`) forces a whole-feature re-cut instead of the default incremental tail."""
    from sgt.intent.theme import build_themes
    from sgt.intent.theme_segment import build_segments

    segments = build_segments(repo, recut)
    themes = build_themes(repo)
    n_ckpt = sum(len(v) for v in segments.values())
    if as_json:
        return _emit_json({"features": len(segments), "checkpoints": n_ckpt, "themes": len(themes)})
    print(f"✓ intent build: {n_ckpt} checkpoint(s) across {len(segments)} feature(s), "
          f"{len(themes)} theme(s)")
    return 0


def _revert(repo: str, target: str, subset: list[str] | None, emit: bool, as_json: bool,
            yes: bool = False) -> int:
    from sgt import state
    from sgt.core import verbs
    from sgt.core.lens import _load_declared
    from sgt.core.store import Store
    from sgt.intent import group
    from sgt.lens.tree import load as load_tree

    all_ops = Store(repo).all_ops()
    declared = _load_declared(repo)
    all_atoms = group.atoms(repo)
    themes = state.load_json(repo, "intent_themes", default={})

    theme_entry = themes.get(target)
    if theme_entry is not None:
        current_shas = {a.commit_sha for a in all_atoms}
        stale = sorted(frozenset(theme_entry["atom_shas"]) - current_shas)
        if stale:
            names = ", ".join(sha[:8] for sha in stale)
            return _fail_json(
                f"run `sgt intent build` to reconcile -- {len(stale)} member commit(s) no longer "
                f"resolve: {names}", as_json,
            )

    resolved = group.resolve_group(target, themes, all_atoms)
    if resolved is None:
        return _fail_json(f"no theme or commit {target!r} found in the intent overlay", as_json)
    kind, member_atoms = resolved

    requires = group.group_requires(member_atoms, all_ops, declared)
    chosen, err = group.apply_subset(member_atoms, requires, subset)
    if err is not None:
        return _fail_json(err, as_json)

    op_ids = frozenset().union(*(a.op_ids for a in chosen)) if chosen else frozenset()
    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}
    commit_shas = frozenset(a.commit_sha for a in chosen if a.commit_sha != group.UNWITNESSED)
    tier = group.tier(op_ids, commit_shas, all_ops, declared, op_leaf)

    if not as_json:
        if len(chosen) > 1:
            print(f"reverting {len(chosen)} atom(s) as one group:")
            for atom in chosen:
                print(f"    {atom.commit_sha[:12]}  {atom.subject}  ({len(atom.op_ids)} op(s))")
        print(f"  tier: {tier} {_tier_badge(tier)}")

    preview = verbs.plan_revert_op_set(repo, target, op_ids)

    from collections import Counter

    from .ideal_edit import _emit_verb_result

    # Focus the feedforward on the feature that owns the most reverted ops (op_leaf already loaded).
    tally = Counter(op_leaf[o] for o in preview.removed if o in op_leaf)
    focus_fid = tally.most_common(1)[0][0] if tally else None
    return _emit_verb_result(repo, preview, emit, as_json, extra={"tier": tier},
                             yes=yes, focus_fid=focus_fid)
