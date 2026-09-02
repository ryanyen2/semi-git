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

`asks_for_ops` is the read side, and it answers for an op SET rather than for a save: what a
reader points at is a chapter, a feature, a symbol's history, or a ◆ row of work spread across
features, and all of those have to be answered by one function or two surfaces will quote different
words back for the same code. `ask_record` is the shape they all render -- an excerpt
(`sgt.intent.gist`) plus whose words they were and how much of the selection they account for --
and `dominant_ask` picks the one a single line quotes.

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


def stint_words(repo: str | Path, shas: frozenset[str] | set[str] | None = None,
                ) -> dict[str, list[dict]]:
    """The segmentation weave's evidence feed (P3, §4d): per commit, every grounded stint as
    `{turn_id, session, ts, text, op_ids}` -- what `segments_for` weighs on boundaries
    (`_dominant_turn`) and names chapters with (`apply_words_labels`). One manifest-store read for
    however many shas; `shas=None` covers every manifested save. A sha with no manifest, or no
    grounded stint, is simply absent -- pre-weave history stays subject-labeled."""
    from sgt.intent.manifest import load_manifests

    manifests = load_manifests(repo)
    out: dict[str, list[dict]] = {}
    for sha in manifests if shas is None else (s for s in shas if s in manifests):
        entries = [
            {"turn_id": s["turn"]["id"], "session": s["turn"]["key"], "ts": s["turn"]["ts"],
             "text": s["turn"]["text"], "op_ids": s["op_ids"],
             # Whose words these are travels WITH them: every surface that renders an ask says so
             # (`_SOURCE`), and a feed that dropped the channel forced each caller to guess.
             "channel": s["turn"]["channel"], "actor": s["turn"]["actor"]}
            for s in derive_stints(manifests, sha, root=repo)["stints"] if s["op_ids"]
        ]
        if entries:
            out[sha] = entries
    return out


def whole_save_turns(repo: str | Path, target: dict | None, sha: str) -> list[dict]:
    """The turns that are explicit claims about the WHOLE save `sha`, as opposed to ambient
    conversation. Two sources: a sha-keyed turn (`-m` via the CLI, or an MCP-carried prompt with
    no chat key -- read from the turn store, not the manifest, because that carry lands *after*
    the window closed and tool_save re-reflects to pick it up), and an `agent`-channel chat turn
    inside the save's window (an MCP client without hooks produces no events, so its deliberate
    carry must not count for *less* than passing no session id at all would). A hook turn is
    never here -- ambient words need event grounding (case 5). ONE rule, shared by the rationale
    emission (`reflect_save`) and the context pack (`sgt.api.checkpoint_context`), so what a save
    claims cannot differ between the why and the asked."""
    from sgt.intent.turns import turns_for

    wide = list(turns_for(repo, sha, key_kind="sha"))
    if target:
        wide += [t for t in target["turns"] if t["key_kind"] == "chat" and t["channel"] == "agent"]
    return wide


def reflect_save(repo: str | Path, sha: str) -> list[str]:
    """Emit rationale records for the save `sha`: one per grounded stint, plus save-wide ones for
    the whole-save claims (sha-keyed turns; `agent`-channel carries in the window). Returns the
    record ids (fresh or pre-existing -- everything here is idempotent). The caller guards; a
    reflection hiccup must never disturb the verb it rides."""
    from sgt.intent.gist import ROW_WIDTH, ask_gist
    from sgt.intent.manifest import load_manifests
    from sgt.intent.rationale import _subject_for, record_rationale

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
        reason = ask_gist(st["turn"]["text"], ROW_WIDTH)
        rid = record_rationale(
            repo, subject=_subject_for(repo, st["op_ids"]), reason=reason, actor="human",
            evidence=[st["turn"]["id"]], recorded_by="stint",
        )
        if rid:
            ids.append(rid)
            emitted.add(reason)
    all_op_ids = [o["id"] for o in target["ops"]]
    if all_op_ids:
        for t in whole_save_turns(repo, target, sha):
            reason = ask_gist(t["text"], ROW_WIDTH)
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


# Where a captured ask came from, in words. Here rather than in each renderer because there are
# three of them -- the CLI, the terminal map, the editor's webview -- in two languages, and "whose
# words are these" is the one thing the reader must not be told differently by two of them. The
# distinction is the capture channel's trust tier (`sgt.intent.turns`): a harness capture is the
# developer's own typing, an agent carry is an agent's claim about it, a note is a paraphrase.
_SOURCE = {
    "hook": "you, in a Claude Code chat",
    "agent": "you, relayed by the assistant",
    "note": "the assistant's note",
    "cli": "your save message",
    "sidecar": "a recorded prompt",
    # The `_atom_prompt` fallback ladder reaches sidecars, save messages and turns alike, so a word
    # that arrived through it has no knowable channel. Say that rather than picking one.
    "recorded": "a recorded prompt",
}


def resumable(session_id: str | None) -> bool:
    """Whether `claude --resume <session_id>` would actually reopen anything.

    Every surface that shows a captured ask offers the way back into the conversation it came from,
    and until this check the offer was made blind -- for a session whose transcript had been
    compacted away, for one recorded on another machine, and (the case that forced this) for the
    replayed history a study bundle ships, where the command is printed beside real words and fails
    when anybody types it. A command that cannot work is worse than no command: it teaches the
    reader that the lines here are decoration.

    The words themselves are the payload and are always there; this only gates the accelerator.
    Reads `CLAUDE_CONFIG_DIR` so an isolated Claude Code install answers for its own transcripts."""
    if not session_id:
        return False
    import os

    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "projects"
    try:
        return any(root.glob(f"*/{session_id}.jsonl"))
    except OSError:  # an unreadable or absent config dir is simply "cannot resume"
        return False


def ask_record(text: str, *, channel: str, actor: str = "human", ts: float | None = None,
               session: str | None = None, claimed: int = 0, scope: str = "stint",
               full: bool = True) -> dict:
    """One captured ask, in the shape every surface renders.

    `gist` is the excerpt to put on a line, `trimmed` whether the prompt held more, `chars` how
    much more (a reader deciding whether to open it wants the size, not a guess), and `source` who
    typed it in words. `full=False` drops the verbatim `text` -- what a list of forty chapters
    sends, where forty prompts would be most of the payload and none of them would be read until
    one is opened."""
    from sgt.intent.gist import CARD_WIDTH, ask_parts

    parts = ask_parts(text, CARD_WIDTH)
    out = {"gist": parts.gist, "trimmed": parts.trimmed, "chars": len(text or ""),
           "channel": channel, "source": _SOURCE.get(channel, channel), "actor": actor,
           "ts": ts, "claude_session_id": session, "resumable": resumable(session),
           "claimed": claimed, "scope": scope}
    if full:
        out["text"] = text
    return out


def asks_for_ops(repo: str | Path, op_ids, shas) -> list[dict]:
    """Every captured ask that grounds any of `op_ids`, in conversation order.

    The one join behind every surface that shows captured words for a *selection* rather than for a
    save: the `asked` attribute on `sgt show`, the checkpoint context pack, the workbench card. It
    has to be one function -- a second copy of "which prompts claim these ops" is a copy that will
    disagree with the first about what a chapter was for, which is the whole thing these surfaces
    exist to say.

    Each ask carries `claimed` (how many of `op_ids` its stint grounds) so a caller can pick the
    dominant one for a one-line render, `scope` ("stint" for grounded ambient words, "save" for an
    explicit claim about the whole save), and both the excerpt and the verbatim text -- the excerpt
    for a row, the text for the reader who opens it. `shas` are the saves that witness these ops
    (chronological); a sha with no manifest contributes only its committed sidecar digest, which is
    how pre-weave history stays honestly empty rather than guessed at.
    """
    from sgt.intent.manifest import load_manifests
    from sgt.intent.prompts import prompt_for

    wanted = frozenset(op_ids)
    manifests = load_manifests(repo)
    order: list[str] = []
    by_key: dict[str, dict] = {}

    def add(key: str, *, text: str, channel: str, actor: str, ts: float | None,
            session: str | None, claimed: int, scope: str) -> None:
        """One ask per turn, its claim summed across the saves it reached (case 4: one prompt, many
        saves). A key seen twice is the same words, not two asks."""
        if not (text or "").strip():
            return
        if key in by_key:
            by_key[key]["claimed"] += claimed
            return
        by_key[key] = ask_record(text, channel=channel, actor=actor, ts=ts, session=session,
                                 claimed=claimed, scope=scope)
        order.append(key)

    for sha in shas:
        target = manifests.get(sha)
        if target is not None:
            for st in derive_stints(manifests, sha, root=repo)["stints"]:
                claimed = len(wanted & frozenset(st["op_ids"]))
                if claimed:
                    t = st["turn"]
                    add(t["id"], text=t["text"], channel=t["channel"], actor=t["actor"],
                        ts=t["ts"], session=t["key"], claimed=claimed, scope="stint")
        save_ops = frozenset(o["id"] for o in target["ops"]) if target else frozenset()
        for t in whole_save_turns(repo, target, sha):
            add(t["id"], text=t["text"], channel=t["channel"], actor=t["actor"], ts=t["ts"],
                session=t["key"] if t["key_kind"] == "chat" else None,
                claimed=len(wanted & save_ops), scope="save")
        digest = prompt_for(repo, sha)
        # A digest that repeats an ask already listed is the same words twice, not a second ask:
        # the sidecar is written FROM a turn, so the two agree by construction more often than not.
        if digest and digest not in {a["text"] for a in by_key.values()}:
            add(f"sidecar:{sha}", text=digest, channel="sidecar", actor="human", ts=None,
                session=None, claimed=len(wanted & save_ops), scope="save")

    return [by_key[k] for k in order]


def dominant_ask(asks: list[dict]) -> dict | None:
    """The one ask to put on a single line: the one grounding the most of the selection, latest
    first on a tie -- a correction is the standing word, the same rule `_dominant_turn` uses for a
    chapter's name. `None` for a selection nothing captured claims."""
    if not asks:
        return None
    return max(asks, key=lambda a: (a["claimed"], a["ts"] or 0.0))
