#!/usr/bin/env python3
"""Replay the conversation behind a study testbed's history, after the fact.

    scripts/study/backfill_capture.py <repo> <bikecount|footfall> [--check]

The testbeds are built by replaying commits (`scripts/study/build_stages.sh`), so no prompt hook was
running while they were made and their capture stores are empty. A participant in the sgt arm would
then meet a tool whose most human attribute -- what each piece of work was asked for, in the words
somebody typed -- is blank on every chapter, which is the one thing the arm is being evaluated on.

So the words are put back: for each commit, the turns of the conversation that produced it, the edit
events that conversation caused, and the per-save manifest that closes the window between the
previous save and this one. Nothing here fakes a *derived* fact. The stints, the rationale records,
the chapter names and the `asked` attribute are all computed afterwards by the ordinary code paths
(`sgt.intent.stint.reflect_save`) from this evidence, exactly as they would have been computed live
-- which is also what makes this a real test of those paths rather than a fixture of their output.

Three details are load-bearing:

* **Windows chain.** A manifest starts where the previous one ended (`record_manifest`), so the
  commits are walked oldest-first and each window's turns and events are written before its manifest
  closes. Out of order, one window swallows the whole history and every ask lands on one save.
* **Event paths are repo-relative.** The live hook records absolute paths, and `_rel_file` resolves
  them against the repo root -- which fails on a participant's machine, where the bundle sits at a
  different path than the build machine. A relative path is taken as already-relative and survives
  the copy.
* **A turn owns the events that follow it** (`derive_stints`, per session), so turns and events
  interleave: each ask, then its own files' edits, then the next ask. Batched at the head of the
  window instead, the last turn typed takes the whole save -- which on the first run handed one
  chapter to the question that produced no code and gave a correction chain's opening ask nothing.

`--check` reports what a repo currently holds instead of writing: the number of saves with a
manifest, how many have a grounded ask, and which have none. Idempotent either way -- turns are
content-addressed, manifests are write-once, rationale is content-addressed -- so re-running adds
nothing and a bundle build can call it unconditionally.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "capture-prompts.json"

# Where in a window the words and the work go: nothing until a third of the way in (the gap after a
# save is somebody reading, not typing), then ask-work-ask-work through to just before the next
# save. Fractions rather than fixed offsets, so a window of four minutes and a window of four days
# both come out correctly ordered.
_WORK_FROM, _WORK_TO = 0.35, 0.90
# A window with no previous save (the first commit) has no start edge to interpolate inside, so its
# turns are placed this many seconds before the commit itself.
_FIRST_WINDOW = 900.0


def _episodes(repo: Path):
    """Every commit that witnesses ops, oldest first, as `(sha, subject, commit_time, [files])`.

    `files` are the repo-relative paths the commit's *ops* touch -- the footprint the stint join
    intersects against (`_op_files`), not `git show --name-only`. They have to be the same paths or
    the replayed events ground nothing: an event on a file no op in this save touched is an event
    the derivation correctly ignores."""
    from sgt.core import opindex
    from sgt.store.gitbind import GitBinding

    gb = GitBinding(str(repo))
    rows = gb.history()  # oldest-first
    ops = list(opindex.index_ops(str(repo)))
    sha_of = opindex.earliest_commit_sha(gb, rows, ops)
    times = gb.commit_times()

    by_sha: dict[str, list] = {}
    for op in ops:
        sha = sha_of.get(op.id)
        if sha:
            by_sha.setdefault(sha, []).append(op)

    out = []
    for sha, _parent, subject in rows:
        witnessed = by_sha.get(sha)
        if not witnessed or sha not in times:
            continue  # a land merge or a commit whose ops were first witnessed earlier
        files: list[str] = []
        for op in witnessed:
            for sym in sorted(op.footprint):
                path = sym.split("::", 1)[0]
                if path and not path.startswith("__") and path not in files:
                    files.append(path)
        out.append((sha, subject, times[sha], files, witnessed))
    return out


def _schedule(prev_end: float | None, end: float, shares: list[list[str]], n_questions: int):
    """When each turn was typed and when each of its edits landed, inside the window `(prev_end,
    end]`.

    Interleaved, not batched, and that is the whole point: a turn owns the events that follow it
    (`derive_stints`, per session), so writing every turn at the head of the window and every event
    after them hands the entire save to whichever turn happened to be typed last. That produced two
    wrong histories on the first run -- a chapter owned by the question that produced no code, and a
    correction chain where the correction claimed all of the work and the original ask claimed none
    of it.

    So each speaking turn gets a block of the window: the turn, then its own files' edits, then the
    next turn. Questions go after the last edit, which is where a turn that produced nothing
    belongs -- and is what makes it own nothing.

    Returns `(turn_times, event_times)`; `event_times[i]` corresponds to `shares` flattened."""
    start = end - _FIRST_WINDOW if prev_end is None else prev_end
    span = max(1.0, end - start)
    blocks = max(1, len(shares))
    block = span * (_WORK_TO - _WORK_FROM) / blocks
    turn_times: list[float] = []
    event_times: list[float] = []
    for i, group in enumerate(shares):
        head = start + span * _WORK_FROM + block * i
        turn_times.append(head)
        step = block / (len(group) + 1)
        event_times.extend(head + step * (j + 1) for j in range(len(group)))
    last = event_times[-1] if event_times else start + span * _WORK_FROM
    for q in range(n_questions):
        turn_times.append(min(last + 1 + q, end - 0.5))
    # Nothing may sit on or past the window's edges: `record_manifest` harvests `start < ts <= end`,
    # so a turn on the previous save's edge is dropped and reads as an ask that was never captured.
    clamp = lambda t: min(max(t, start + 1.0), end - 0.5)
    return [clamp(t) for t in turn_times], [clamp(t) for t in event_times]


def backfill(repo: Path, project: str, verbose: bool = True) -> dict:
    """Write the capture stores for `project`'s testbed at `repo`. Returns a small report."""
    from sgt.intent.activity import record_activity
    from sgt.intent.manifest import record_manifest
    from sgt.intent.stint import reflect_save
    from sgt.intent.turns import record_turn

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8")).get(project)
    if not fixture:
        sys.exit(f"no prompts for {project!r} in {FIXTURE.name}")

    episodes = _episodes(repo)
    if not episodes:
        sys.exit(f"{repo} has no committed ops -- mine it first (`sgt log`)")

    unmatched = set(fixture) - {subject for _s, subject, _t, _f, _o in episodes}
    if unmatched:
        # Loud, because the usual cause is a testbed rebuild that reworded a commit, and the quiet
        # failure is a bundle that ships with the words missing from exactly the work the task is
        # about -- invisible from every angle a builder would look at.
        sys.exit(f"{project}: no commit matches {sorted(unmatched)} -- the testbed's subjects "
                 f"changed; update {FIXTURE.name}")

    prev_end: float | None = None
    turns_written = events_written = 0
    silent: list[str] = []
    for sha, subject, end, files, ops in episodes:
        entry = fixture.get(subject)
        if entry:
            prompts = entry["prompts"]
            speaking = [p for p in prompts if not p.get("question")]
            # The files are dealt out across the speaking turns in order, so a correction chain
            # ends with each turn owning the part of the save it was followed by. One turn takes
            # them all, which is the ordinary case.
            shares: list[list[str]] = [[] for _ in speaking] or [[]]
            for i, path in enumerate(files):
                shares[i % len(shares)].append(path)
            questions = [p for p in prompts if p.get("question")]
            turn_times, event_times = _schedule(prev_end, end, shares, len(questions))

            for p, ts in zip(speaking + questions, turn_times):
                record_turn(repo, key=entry["session"], key_kind="chat", actor="human",
                            channel="hook", text=p["text"], ts=ts)
                turns_written += 1
            for path, ts in zip([f for group in shares for f in group], event_times):
                record_activity(repo, tool="Edit", file=path, session_id=entry["session"], ts=ts)
                events_written += 1
        else:
            silent.append(f"{sha[:7]} {subject}")

        record_manifest(repo, sha=sha, ops=ops, end=end, prev_save_ts=prev_end)
        reflect_save(repo, sha)
        prev_end = end

    report = {"saves": len(episodes), "turns": turns_written, "events": events_written,
              "silent": silent}
    if verbose:
        print(f"  {report['turns']} turn(s), {report['events']} edit event(s) across "
              f"{report['saves']} save(s)")
        for row in silent:
            print(f"  no words for {row}  (kept as a save nobody prompted)")
    return report


def check(repo: Path) -> int:
    """What the repo holds now: one line per save, and a non-zero exit if nothing is grounded.

    The exit code is what a bundle build wants -- a repo where the backfill silently grounded
    nothing looks identical to one where it worked, until a participant asks a chapter what it was
    for."""
    from sgt.intent.manifest import load_manifests
    from sgt.intent.stint import derive_stints
    from sgt.intent.gist import ask_gist

    manifests = load_manifests(repo)
    if not manifests:
        print("no capture manifests -- this repo's history has no recorded conversation")
        return 1
    grounded = 0
    for sha, m in sorted(manifests.items(), key=lambda kv: kv[1]["end"]):
        stints = [s for s in derive_stints(manifests, sha, root=repo)["stints"] if s["op_ids"]]
        residual = len(derive_stints(manifests, sha, root=repo)["residual_op_ids"])
        if stints:
            grounded += 1
            best = max(stints, key=lambda s: len(s["op_ids"]))
            print(f'  {sha[:7]}  {len(stints)} stint(s), {residual} unclaimed edit(s)  '
                  f'“{ask_gist(best["turn"]["text"], 64)}”')
        else:
            print(f"  {sha[:7]}  no grounded ask, {residual} unclaimed edit(s)")
    print(f"  {grounded}/{len(manifests)} save(s) have a grounded ask")
    return 0 if grounded else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("project", nargs="?", choices=("bikecount", "footfall"))
    ap.add_argument("--check", action="store_true", help="report what is there; write nothing")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not (repo / ".sgt").is_dir():
        sys.exit(f"{repo} is not an sgt repo")
    if args.check:
        return check(repo)
    if not args.project:
        sys.exit("a project is required when writing (bikecount|footfall)")
    backfill(repo, args.project)
    return check(repo)


if __name__ == "__main__":
    raise SystemExit(main())
