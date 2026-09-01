"""Stint derivation (capture weave P2, docs/design/2026-09-01-capture-weave.md §4c): the
deterministic prompt→op join, computed from capture manifests -- never stored, never guessed.

A **stint** is one turn's grounded slice of a save: the turn, the activity events its session
produced while that turn was the session's latest word, and the ops of the closing save whose
footprint files those events touched. Op membership is a pure function of captured evidence --
the same discipline as segmentation's safety invariant (`sgt.intent.segment`): no LLM, no
similarity score, no EM. What the evidence cannot ground falls to the residual, so a hand-typed
edit is never attributed to the nearest prompt just because it was nearby in time.

The rules, each carrying one of the design's cases:

- An event's owning turn is its session's latest turn at or before the event -- looked up across
  *all* manifests, so a turn whose session kept working silently through later saves keeps owning
  that work (case 4, one prompt / many saves) without ever being re-harvested.
- Ownership is per-session, never global time (case 6, two agent windows interleaved).
- A turn that owns no events is not a stint (case 5, the question that produced no code); a stint
  whose files ground no ops claims nothing (case 9, the abandoned prompt -- claiming requires
  fresh events in the window being derived, so an old ask never reopens onto later work).
- An op claimed by several stints keeps every claim (case 8, the correction chain: "add auth" then
  "no, sessions not JWTs" are BOTH the why of the code that survived -- `for_op` renders them in
  order, and supersedence stays a human judgement via `sgt intent edit`).

`reflect_save` is the save-beat emitter: it turns each grounded stint into a standard
`sgt.intent.rationale` record (actor `human`, unconfirmed, evidence = the turn ids), plus
save-wide records for the words that are explicit claims about the whole save -- a sha-keyed turn
(`-m`, or an MCP-carried prompt with no chat key) and an `agent`-channel carry in the window (an
MCP client without hooks produces no events to ground through). Everything downstream -- `sgt why`,
`intent review`, `intent edit`, supersedence -- works unchanged on these records. Idempotent all
the way down (manifests are write-once, rationale is content-addressed), so re-reflecting a sha
is a no-op.
"""

from __future__ import annotations

from bisect import bisect_right
from pathlib import Path


def _rel_file(file: str | None, root: Path) -> str | None:
    """An event's file as a repo-relative path, `None` for a file outside the repo (pre-guard
    captures) or no file at all. A relative path is taken as already repo-relative."""
    if not file:
        return None
    p = Path(file)
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except ValueError:
        return None


def _op_files(op_entry: dict) -> frozenset[str]:
    """The repo-relative files a manifest op anchor touches -- its footprint symbols' path halves
    (a whole-file pseudo-symbol is its own path already)."""
    return frozenset(sym.split("::", 1)[0] for sym in op_entry["symbols"])


def derive_stints(manifests: dict[str, dict], sha: str, root: str | Path) -> dict:
    """The stints of the save `sha` closed, derived from the manifest store alone.

    Returns `{"stints": [...], "residual_op_ids": [...]}` where each stint is
    `{"turn": <turn record>, "events": [...], "files": [...], "op_ids": [...]}`, stints ordered by
    their turn's time. Empty when `sha` has no manifest (a pre-weave save) -- honest absence, the
    caller renders nothing rather than a guess."""
    target = manifests.get(sha)
    if target is None:
        return {"stints": [], "residual_op_ids": []}
    root = Path(root)

    # Every chat turn up to this window's close, per session, time-ordered: the ownership index.
    # Manifest copies only (self-sufficiency: this must survive a future turn-store GC); the
    # target's own turns are naturally included since its end <= its end.
    by_session: dict[str, list[dict]] = {}
    for m in manifests.values():
        if m["end"] > target["end"]:
            continue
        for t in m["turns"]:
            if t["key_kind"] == "chat":
                by_session.setdefault(t["key"], []).append(t)
    for turns in by_session.values():
        turns.sort(key=lambda t: (t["ts"], t["seq"]))

    # Own each of the target window's events: the session's latest turn at or before it.
    owned: dict[str, list[dict]] = {}  # turn id -> events
    for e in target["events"]:
        session = e.get("session_id")
        turns = by_session.get(session) if session else None
        if not turns:
            continue
        i = bisect_right([t["ts"] for t in turns], e["ts"])
        if i == 0:
            continue  # the session's first word came after this event: nothing owns it
        owned.setdefault(turns[i - 1]["id"], []).append(e)

    turn_by_id = {t["id"]: t for turns in by_session.values() for t in turns}
    claimed: set[str] = set()
    stints = []
    for tid, events in owned.items():
        files = frozenset(f for e in events if (f := _rel_file(e.get("file"), root)))
        op_ids = [o["id"] for o in target["ops"] if _op_files(o) & files]
        claimed.update(op_ids)
        stints.append({"turn": turn_by_id[tid], "events": events,
                       "files": sorted(files), "op_ids": op_ids})
    stints.sort(key=lambda s: (s["turn"]["ts"], s["turn"]["seq"]))
    residual = [o["id"] for o in target["ops"] if o["id"] not in claimed]
    return {"stints": stints, "residual_op_ids": residual}


def reflect_save(repo: str | Path, sha: str) -> list[str]:
    """Emit rationale records for the save `sha`: one per grounded stint, plus save-wide ones for
    the whole-save claims (sha-keyed turns; `agent`-channel carries in the window). Returns the
    record ids (fresh or pre-existing -- everything here is idempotent). The caller guards; a
    reflection hiccup must never disturb the verb it rides."""
    from sgt.intent.manifest import load_manifests
    from sgt.intent.rationale import _subject_for, record_rationale
    from sgt.intent.turns import turns_for
    from sgt.intent.working import _first_line

    manifests = load_manifests(repo)
    target = manifests.get(sha)
    if target is None:
        return []
    ids = []
    emitted: set[str] = set()  # reasons already claimed this save; a save-wide twin adds nothing
    derived = derive_stints(manifests, sha, root=repo)
    for st in derived["stints"]:
        if not st["op_ids"]:
            continue
        reason = _first_line(st["turn"]["text"])
        rid = record_rationale(
            repo, subject=_subject_for(repo, st["op_ids"]), reason=reason, actor="human",
            evidence=[st["turn"]["id"]], recorded_by="stint",
        )
        if rid:
            ids.append(rid)
            emitted.add(reason)
    # Save-wide words ground every op the save minted. Two sources, both explicit claims about
    # this very save rather than ambient conversation: a sha-keyed turn (`-m` via the CLI, or an
    # MCP-carried prompt with no chat key -- read from the turn store, not the manifest, because
    # that carry lands *after* the window closed and tool_save re-reflects to pick it up), and an
    # `agent`-channel chat turn inside the window (an MCP client without hooks produces no events,
    # so its deliberate carry must not ground *less* than passing no session id at all would).
    # A hook turn never grounds save-wide -- ambient words need event grounding (case 5).
    all_op_ids = [o["id"] for o in target["ops"]]
    if all_op_ids:
        wide = list(turns_for(repo, sha, key_kind="sha"))
        wide += [t for t in target["turns"] if t["key_kind"] == "chat" and t["channel"] == "agent"]
        for t in wide:
            reason = _first_line(t["text"])
            if reason in emitted:  # its stint (or the hook twin's) already said this
                continue
            rid = record_rationale(
                repo, subject=_subject_for(repo, all_op_ids), reason=reason, actor="human",
                evidence=[t["id"]], recorded_by="stint",
            )
            if rid:
                ids.append(rid)
                emitted.add(reason)
    return ids
