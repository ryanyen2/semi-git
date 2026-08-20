#!/usr/bin/env python3
"""WP-V2 step 1: recover "one human request -> the file edits it caused" from Claude Code transcripts.

    python docs/eval/v2-transcripts/extract_edits.py <~/.claude/projects/-Users-...> --out <dir>

Why this is not a one-liner. A session `.jsonl` is not a conversation, it is an event log: in the
smallest CodeNav session, 108 of 447 records are typed `user` and only **4** of those carry a
human-typed string -- the other 104 are `tool_result` blocks the harness files under the same type.
Segmenting on "type == user" would report 108 requests where there were 4. So the boundary is
`type == "user" AND isinstance(message.content, str)`, which is what the plan specifies.

Three rules fixed here before any number is computed (R2/R8), each with its count reported so the
filter is auditable rather than invisible:

1. **A string is not automatically a human request.** The harness writes several kinds of machine
   text into the same slot: slash-command expansions, interrupt markers, compaction hand-offs,
   pasted-file caveats, bare `<system-reminder>` blocks. These are classified by
   `machine_text_class` and excluded from starting a segment; their edits fold into the human
   request they interrupt, because that is whose intent the edits serve. A `/loop` firing is
   machine text by this rule -- it re-delivers an earlier human prompt, and counting each firing as
   a fresh request would inflate the request count of any long autonomous session.

2. **A failed edit is not an edit.** An `Edit` whose `tool_result` is an error changed no file.
   Counting it would put a symbol in a request's footprint that the request never touched, which
   inflates recall's denominator with work that does not exist.

3. **A subagent's edits belong to the request that spawned it.** The `Agent` tool's result carries
   `agentId`, and the subagent transcript is `<session>/subagents/agent-<agentId>.jsonl`. Its edits
   are attributed to the enclosing human request, tagged with their source so the fold is visible.

Output is one JSON per project: requests in session order, each with its edits. No symbol mapping
and no clustering here -- step 2 does that, and keeping them apart is what makes a low match rate
readable as a fact about the mapper rather than about the parser.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Cap on stored request text. The full text stays in the transcript; what is stored here has to be
# enough for a human and an LLM to judge "same request?" in step 5, and 4000 characters is past the
# point where either is reading more of the prompt rather than more of the context around it.
TEXT_CAP = 4000


def machine_text_class(text: str) -> str | None:
    """Which kind of harness-written string this is, or None if it reads as a human request."""
    t = text.lstrip()
    if t.startswith("<command-name>") or t.startswith("<command-message>"):
        return "slash-command expansion"
    if t.startswith("<local-command-stdout>") or t.startswith("<local-command-stderr>"):
        return "local command output"
    if t.startswith("[Request interrupted"):
        return "interrupt marker"
    if t.startswith("<local-command-caveat>") or t.startswith("Caveat: The messages below"):
        return "local command caveat"
    if "This session is being continued from a previous conversation" in t[:400]:
        return "compaction hand-off"
    if t.startswith("<system-reminder>") and t.rstrip().endswith("</system-reminder>"):
        return "system reminder only"
    # Harness text found by bucketing every string-content `user` record in all four projects by its
    # opening 52 characters, rather than by guessing what the harness writes. The two that matter are
    # below, and both were splitting real requests in half: a `<task-notification>` lands *during* an
    # autonomous run, and a stop-hook re-delivery lands at every stop. In eico, four of the twelve
    # requests that touched code were one of these -- 42, 41, 7 and 4 symbols filed under a
    # notification rather than under the sentence that asked for the work. Over-splitting the ground
    # truth understates sgt's precision (pairs it correctly groups are scored as different requests),
    # so this is a defect in the harness-facing direction as well as the sgt-facing one.
    if t.startswith("<task-notification>"):
        return "task notification"
    if t.startswith("A session-scoped Stop hook is now active with condition:"):
        return "stop-hook re-delivery"
    if t.startswith("[Image:") and t.rstrip().endswith("]"):
        return "image placeholder only"
    if t.startswith("/") and len(t.split()) == 1:
        return "bare slash command"
    if not t.strip():
        return "empty"
    return None


# A request whose text is this short says "keep going", not "do this": `resume`, `ok sure`, `(a)`.
# It is human-typed, so it stays -- dropping it would delete real work from the corpus -- but it is
# counted, because a human or LLM coder in step 5 cannot judge "same request?" from `resume`, and
# because a corpus where the code-touching requests are mostly continuations is telling us that in
# agentic sessions the request boundary is not the intent boundary.
THIN_CHARS = 24



def tool_uses(rec: dict) -> list[dict]:
    if rec.get("type") != "assistant":
        return []
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


def tool_results(rec: dict) -> list[dict]:
    """`tool_result` blocks, which arrive on records typed `user`."""
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]


def edit_target(name: str, inp: dict) -> str | None:
    if name == "NotebookEdit":
        return inp.get("notebook_path")
    return inp.get("file_path")


def scan(path: Path, source: str, subagent_dir: Path | None, stats: Counter) -> list[dict]:
    """One transcript -> a list of `{"kind": "request"|"edit", ...}` events in file order.

    Flattened rather than nested so a subagent's events can be spliced into the parent stream at the
    point its result lands, without the caller needing to know how either file is shaped.
    """
    events: list[dict] = []
    pending: dict[str, dict] = {}          # tool_use id -> edit awaiting its result
    for line in path.open():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            stats["unparseable lines"] += 1
            continue

        if rec.get("type") == "user":
            content = (rec.get("message") or {}).get("content")
            # A subagent's *prompt* is also a `user` record with string content -- written by the
            # model, not the human. The first run of this script let those start segments, which both
            # invented requests nobody made and cut the real request in half at the point it
            # delegated: the parent's later edits were filed under the subagent's brief. Subagent
            # files contribute edits only; the request boundary is a human-typed turn by definition.
            if isinstance(content, str) and source == "main":
                cls = machine_text_class(content)
                if cls:
                    stats[f"excluded: {cls}"] += 1
                else:
                    stats["human requests"] += 1
                    events.append({
                        "kind": "request",
                        "request_id": rec.get("promptId") or rec.get("uuid"),
                        "session": rec.get("sessionId"),
                        "ts": rec.get("timestamp"),
                        "branch": rec.get("gitBranch"),
                        "text": content[:TEXT_CAP],
                        "text_truncated": len(content) > TEXT_CAP,
                        "thin": len(" ".join(content.split())) <= THIN_CHARS,
                    })
                    stats["thin requests (continuations)"] += (
                        1 if len(" ".join(content.split())) <= THIN_CHARS else 0)
            # A record typed `user` also carries tool results -- both for edits and for Agent calls.
            for block in tool_results(rec):
                got = pending.pop(block.get("tool_use_id"), None)
                if got is not None:
                    if block.get("is_error"):
                        stats["excluded: failed edit"] += 1
                    else:
                        stats["edits"] += 1
                        events.append(got)
            tur = rec.get("toolUseResult")
            if isinstance(tur, dict) and tur.get("agentId") and subagent_dir is not None:
                sub = subagent_dir / f"agent-{tur['agentId']}.jsonl"
                if sub.exists():
                    stats["subagent transcripts folded in"] += 1
                    events.extend(scan(sub, f"agent-{tur['agentId']}", None, stats))
                else:
                    stats["subagent transcripts missing"] += 1

        for block in tool_uses(rec):
            if block.get("name") not in EDIT_TOOLS:
                continue
            target = edit_target(block["name"], block.get("input") or {})
            if not target:
                stats["excluded: edit with no path"] += 1
                continue
            # `old`/`new` are stored in full and deliberately not truncated. Step 2 has to decide
            # *which symbol in the file* an edit touched, and it can only do that by locating the
            # text; a capped `new` on a `Write` would silently turn "these three functions" into
            # "the first N characters of the file", which is exactly the kind of quiet
            # under-attribution that makes a low match rate unreadable.
            inp = block.get("input") or {}
            pending[block["id"]] = {
                "kind": "edit", "tool": block["name"], "path": target, "source": source,
                "cwd": rec.get("cwd"),
                "old": inp.get("old_string"),
                "new": inp.get("new_string") if block["name"] != "Write" else inp.get("content"),
                "edits": inp.get("edits"),        # MultiEdit carries a list instead
            }
    # A tool_use whose result never lands (session ended mid-call, or the user quit) is not an edit.
    stats["excluded: edit with no result"] += len(pending)
    return events


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_dir", type=Path, help="a ~/.claude/projects/-Users-... directory")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    proj = args.project_dir.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    stats: Counter = Counter()
    requests: list[dict] = []
    orphan_edits = 0
    # Chronological, and `current` carries *across* session boundaries. A `/compact` or a `--resume`
    # opens a new transcript in the middle of the work, so its first records are edits with no
    # preceding human request in that file -- and dropping those cost 42% of CodeNav's edits and 18%
    # of semi-git's, which is a far larger distortion than the one the harness-text filter fixed. The
    # request they belong to is the last one typed before the split, which is the last one seen in
    # project order. An unrelated session resets `current` on its own opening request, so only the
    # pre-first-request window can inherit; those edits are tagged `carried` so the fold stays
    # visible and a sensitivity check can drop them.
    def first_ts(p: Path) -> str:
        for line in p.open():
            try:
                ts = json.loads(line).get("timestamp")
            except json.JSONDecodeError:
                continue
            if ts:
                return ts
        return ""

    current: dict | None = None
    sessions = sorted(proj.glob("*.jsonl"), key=first_ts)
    for sess in sessions:
        stats["sessions"] += 1
        subdir = proj / sess.stem / "subagents"
        for ev in scan(sess, "main", subdir if subdir.exists() else None, stats):
            if ev["kind"] == "request":
                current = {k: v for k, v in ev.items() if k != "kind"}
                current["edits"] = []
                requests.append(current)
            elif current is None:
                # Only reachable now for edits before the *project's* first human request.
                orphan_edits += 1
            else:
                edit = {k: v for k, v in ev.items() if k != "kind"}
                edit["carried"] = sess.stem != current["session"]
                stats["edits carried across a session split"] += 1 if edit["carried"] else 0
                current["edits"].append(edit)

    stats["edits before any request (dropped)"] = orphan_edits
    report = {
        "project_dir": str(proj),
        "sessions": [s.name for s in sessions],
        "n_requests": len(requests),
        "n_requests_with_edits": sum(1 for r in requests if r["edits"]),
        "n_edits": sum(len(r["edits"]) for r in requests),
        "stats": dict(sorted(stats.items())),
        "requests": requests,
    }
    name = proj.name.split("-repos-", 1)[-1] or proj.name
    (out / f"edits-{name}.json").write_text(json.dumps(report, indent=1))

    print(f"{proj.name}")
    print(f"  {stats['sessions']} sessions  ·  {len(requests)} human requests  ·  "
          f"{report['n_requests_with_edits']} of them edited a file  ·  {report['n_edits']} edits")
    for k, v in sorted(stats.items()):
        if k not in ("sessions", "human requests", "edits"):
            print(f"    {k}: {v}")
    print(f"  -> {out / f'edits-{name}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
