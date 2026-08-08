#!/usr/bin/env python3
"""A compact orientation block for a coding agent arriving in an sgt-tracked repo.

An agent that wants to know "where am I, is anyone mid-something, what needs a human" would
otherwise make three or four separate calls and pay for their full payloads. Measured on a
290-commit repo, the naive combination costs roughly 5,100 tokens (`sgt now` ~470, `sgt log`
~1,380, `sgt status` ~3,300) and most of that is detail an agent will not act on. This prints the
actionable subset as plain text in a few hundred tokens.

It is deliberately a script and not an MCP tool, so it works identically in Claude Code, Codex, or
any harness that can run a shell command -- MCP may not be present, but Bash almost always is.

    python -m scripts.sgt_brief              # the brief for the current repo
    python -m scripts.sgt_brief --json       # same content, machine-readable
    python -m scripts.sgt_brief --repo PATH

Exit status is 0 when the repo is sgt-tracked (whatever its state), and 2 when it is not, so a
caller can branch on "is sgt even in play here" without parsing anything.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

MAX_ITEMS = 5  # per list; a brief that grows without bound stops being a brief


def _ellipsize(text: str, width: int = 56) -> str:
    text = " ".join((text or "").split())  # collapse newlines/runs; this goes on one line
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _clip(items, limit=MAX_ITEMS):
    """(shown, hidden_count) -- so the caller can say what it left out instead of truncating
    silently, which would read as "that was all of them"."""
    items = list(items)
    return items[:limit], max(0, len(items) - limit)


def collect(repo: str) -> dict:
    """The brief as data. Reads `now_view` (the one thin, fast assembler) plus the two scalars that
    change what an agent should do next, and nothing else -- every extra view here is context the
    agent pays for on arrival, before it knows whether it needs any of it."""
    from sgt.api import now_view
    from sgt.core.lens import sync_status

    now = now_view(repo)
    needs, inflight = now["needs_you"], now["in_flight"]
    action = now["next_action"]

    forks, forks_hidden = _clip(f.get("symbol") for f in needs["forks"])
    plans, plans_hidden = _clip(
        {"session_id": p["session_id"], "remaining": p["pending_count"]}
        for p in needs["stalled_plans"]
    )
    # Recent work is orientation, not a changelog: three clipped headlines say "this is the
    # neighbourhood you're in" and a fifth full commit subject says nothing more for several times
    # the tokens. `headline` rather than `subject` so a `wip`/`sss` commit still names its feature.
    recent, recent_hidden = _clip(
        (_ellipsize(c.get("headline") or c.get("subject") or "") for c in now["recently_done"]),
        limit=3,
    )
    return {
        "tracked": True,
        "unsaved_ops": inflight["total_op_count"],
        "unsaved_features": len(inflight["affected"]),
        "blocked_by": needs.get("paused_operation"),
        "history_rewritten": bool(needs.get("history_rewritten")),
        "open_forks": forks,
        "open_forks_hidden": forks_hidden,
        "stalled_plans": plans,
        "stalled_plans_hidden": plans_hidden,
        "pending_reviews": len(needs["reviews"]),
        "recently_done": recent,
        "recently_done_hidden": recent_hidden,
        "next_action": {"label": action["label"], "command": action["command"]},
        "sync_complete": bool(sync_status(repo).get("complete")),
    }


def render(brief: dict) -> str:
    """Plain text, no ANSI, no box drawing. This lands in a transcript the human may read, and
    terminal control codes there are noise that costs tokens and renders as garbage."""
    out = ["sgt: tracked repo"]

    if brief["blocked_by"]:
        out.append(f"  BLOCKED: a paused git {brief['blocked_by']} — sgt cannot record anything "
                   f"until it is finished or aborted")
    if brief["history_rewritten"]:
        out.append("  BLOCKED: git history moved backward; sgt's recorded state is stale "
                   "(`sgt advanced resync`)")

    if brief["unsaved_ops"]:
        out.append(f"  unsaved: {brief['unsaved_ops']} edit(s) across "
                   f"{brief['unsaved_features']} feature(s) — the human saves, not you")
    else:
        out.append("  unsaved: nothing")

    if brief["open_forks"]:
        more = f" (+{brief['open_forks_hidden']} more)" if brief["open_forks_hidden"] else ""
        out.append(f"  needs a human: fork(s) on {', '.join(brief['open_forks'])}{more}")
    if brief["stalled_plans"]:
        for p in brief["stalled_plans"]:
            out.append(f"  stalled plan {p['session_id']}: {p['remaining']} step(s) left "
                       f"(adopt it before continuing that work)")
        if brief["stalled_plans_hidden"]:
            out.append(f"  (+{brief['stalled_plans_hidden']} more stalled plan(s))")
    if brief["pending_reviews"]:
        out.append(f"  needs a human: {brief['pending_reviews']} pending review(s)")

    if brief["recently_done"]:
        out.append("  recent: " + " | ".join(brief["recently_done"]))

    action = brief["next_action"]
    suffix = f"  ({action['command']})" if action["command"] else ""
    out.append(f"  next: {action['label']}{suffix}")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    repo = pathlib.Path(args.repo)
    if not (repo / ".sgt").is_dir():
        message = "sgt: not a tracked repo (no .sgt/) — use plain git here"
        print(json.dumps({"tracked": False, "message": message}) if args.as_json else message)
        return 2

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    brief = collect(str(repo))
    print(json.dumps(brief, indent=2) if args.as_json else render(brief))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
