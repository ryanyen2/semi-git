#!/usr/bin/env python3
"""Turn one participant's session into something a person can read in ten minutes.

The console shows numbers and figures. This is the other thing you want after a
session: the whole thing as a narrative -- every command, every prompt, every
answer, in the order it happened, with the think-aloud interleaved. It is what
you actually read when you are coding a session, and what you paste into a
findings document when something went wrong.

  study-debrief.py <code> [--emulator] [--out FILE] [--notes DIR]

`--notes` points at the participant's study folder so the think-aloud file can
be woven in at the right timestamps.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT = "sem-git"
API_KEY = "AIzaSyDsFEnfbmk2Muj1amaYVvIsajEQM8OukNY"


def base_url(emulator: str | None) -> str:
    if emulator:
        return f"http://{emulator}/v1/projects/{PROJECT}/databases/(default)/documents"
    return f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"


def token(emulator: str | None) -> str:
    if emulator:
        return "owner"
    req = urllib.request.Request(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={API_KEY}",
        data=json.dumps({"returnSecureToken": True}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["idToken"]


def decode(value: dict):
    if "nullValue" in value:
        return None
    for key, cast in (
        ("booleanValue", bool),
        ("integerValue", int),
        ("doubleValue", float),
        ("stringValue", str),
        ("timestampValue", str),
    ):
        if key in value:
            return cast(value[key])
    if "arrayValue" in value:
        return [decode(v) for v in value["arrayValue"].get("values", [])]
    if "mapValue" in value:
        return {k: decode(v) for k, v in value["mapValue"].get("fields", {}).items()}
    return None


def fetch(path: str, base: str, tok: str, page_size: int = 300) -> list[dict]:
    """A collection, following pagination; or a single document as a one-item list."""
    out: list[dict] = []
    page = None
    while True:
        url = f"{base}/{path}?pageSize={page_size}"
        if page:
            url += f"&pageToken={page}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {tok}")
        try:
            body = json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception as exc:
            print(f"  (could not read {path}: {exc})", file=sys.stderr)
            return out
        if "fields" in body:
            return [{"_id": path.rsplit("/", 1)[-1], **{k: decode(v) for k, v in body["fields"].items()}}]
        for doc in body.get("documents", []):
            out.append(
                {"_id": doc["name"].rsplit("/", 1)[-1], **{k: decode(v) for k, v in (doc.get("fields") or {}).items()}}
            )
        page = body.get("nextPageToken")
        if not page:
            return out


def clock(ms: int | None) -> str:
    if not ms:
        return "        "
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime("%H:%M:%S")


def mins(ms: float | None) -> str:
    if not ms:
        return "—"
    s = int(ms / 1000)
    return f"{s // 60}m{s % 60:02d}s"


def read_think_aloud(notes_dir: Path | None) -> list[tuple[int, str]]:
    """Timestamped lines from the participant's own running commentary."""
    if not notes_dir:
        return []
    out: list[tuple[int, str]] = []
    for candidate in notes_dir.rglob("think-aloud*"):
        try:
            text = candidate.read_text(errors="replace")
        except Exception:
            continue
        stamp = None
        buffer: list[str] = []
        for line in text.splitlines():
            m = re.match(r"^\s*(?:##+\s*)?\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?", line)
            if m:
                if stamp is not None and buffer:
                    out.append((stamp, "\n".join(buffer).strip()))
                buffer = [line]
                hh, mm, *rest = m.group(1).split(":")
                ss = rest[0] if rest else "0"
                today = datetime.now().astimezone().replace(
                    hour=int(hh), minute=int(mm), second=int(ss), microsecond=0
                )
                stamp = int(today.timestamp() * 1000)
            else:
                buffer.append(line)
        if stamp is not None and buffer:
            out.append((stamp, "\n".join(buffer).strip()))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("--emulator", nargs="?", const="127.0.0.1:8080", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--notes", default=None)
    args = ap.parse_args()

    base = base_url(args.emulator)
    tok = token(args.emulator)
    code = args.code

    participant = (fetch(f"participants/{code}", base, tok) or [{}])[0]
    responses = fetch(f"participants/{code}/responses", base, tok)
    requests_ = fetch(f"participants/{code}/requests", base, tok)
    events = sorted(fetch(f"participants/{code}/events", base, tok, 1000), key=lambda e: e.get("ts") or 0)
    devices = fetch(f"participants/{code}/devices", base, tok)
    scoring = fetch(f"participants/{code}/scoring", base, tok)
    notes = fetch(f"participants/{code}/notes", base, tok)
    aloud = read_think_aloud(Path(args.notes) if args.notes else None)

    L: list[str] = []
    add = L.append

    label = participant.get("label", code[:8])
    add(f"# Session debrief: {label}")
    add("")
    blocks = participant.get("blocks") or []
    for b in blocks:
        add(f"- Half {b.get('half')}: **{b.get('condition')}** on {b.get('project')} ({b.get('label')})")
    add(f"- Status: {participant.get('status')} · reached step `{participant.get('currentStep')}`")
    add(f"- {len(events)} recorded events, {len(requests_)} requests opened, {len(responses)} forms")
    for d in devices:
        checks = d.get("checks") or {}
        bad = [k for k, v in checks.items() if not v.get("ok")]
        add(
            f"- Machine `{d.get('deviceId','?')}` ({d.get('os','?')}): "
            f"{len(checks) - len(bad)}/{len(checks)} checks passed"
            + (f", failed: {', '.join(bad)}" if bad else "")
        )
    add("")

    # ---- what they were asked to do, and how it went ----------------------
    add("## Requests")
    add("")
    add("| Request | Half | Condition | Active | Cap hit | They said | Confidence | Scored |")
    add("|---|---|---|---|---|---|---|---|")
    for r in sorted(requests_, key=lambda x: (x.get("half") or 0, x.get("requestId") or "")):
        s = next((x for x in scoring if x["_id"] == f"{r.get('requestId')}-h{r.get('half')}"), {})
        score = "—" if s.get("score") is None else f"{s.get('score')}/{s.get('outOf')}"
        add(
            f"| {r.get('requestId')} | {r.get('half')} | {r.get('condition')} | "
            f"{mins(r.get('activeMs') or r.get('elapsedMs'))} | {'yes' if r.get('hitCap') else 'no'} | "
            f"{r.get('selfReport') or '—'} | {r.get('confidence') if r.get('confidence') is not None else '—'} | {score} |"
        )
    add("")
    for r in sorted(requests_, key=lambda x: (x.get("half") or 0, x.get("requestId") or "")):
        if r.get("answer"):
            add(f"**{r.get('requestId')} (half {r.get('half')}) answer.** {r['answer']}")
            add("")

    # ---- the shape of the work -------------------------------------------
    kinds = Counter(e.get("kind") for e in events)
    add("## What the machine recorded")
    add("")
    add(", ".join(f"{n} {k}" for k, n in kinds.most_common()) or "nothing")
    add("")

    prompts = [e for e in events if e.get("kind") == "prompt"]
    if prompts:
        add(f"### The {len(prompts)} things they asked the assistant")
        add("")
        for e in prompts:
            add(f"- `{clock(e.get('ts'))}` {(e.get('text') or '').strip()}")
        add("")

    commands = [e for e in events if e.get("kind") == "command"]
    if commands:
        failed = [c for c in commands if c.get("ok") is False]
        add(f"### Commands they ran ({len(commands)}, {len(failed)} failed)")
        add("")
        verbs = Counter()
        for c in commands:
            text = (c.get("text") or "").split()
            verbs[" ".join(text[:2])] += 1
        add("Most used: " + ", ".join(f"`{v}` ×{n}" for v, n in verbs.most_common(12)))
        add("")
        if failed:
            add("Failed:")
            for c in failed:
                add(f"- `{clock(c.get('ts'))}` `{c.get('text')}` → exit {c.get('exitCode')}")
            add("")

    # ---- the whole thing, in order ---------------------------------------
    add("## The session, in order")
    add("")
    add("```")
    merged: list[tuple[int, str]] = []
    for e in events:
        ts = e.get("ts") or 0
        kind = e.get("kind")
        if kind == "prompt":
            merged.append((ts, f"ASK   {(e.get('text') or '')[:150]}"))
        elif kind == "command":
            mark = "!" if e.get("ok") is False else " "
            merged.append((ts, f"RUN  {mark} {(e.get('text') or '')[:150]}"))
        elif kind == "tool":
            merged.append((ts, f"TOOL   {e.get('name')} {(e.get('text') or '')[:110]}"))
        elif kind == "marker":
            merged.append((ts, f"----   request {e.get('requestId')} {e.get('name')}"))
        elif kind == "session":
            merged.append((ts, f"       [{e.get('name')}]"))
    for ts, text in aloud:
        merged.append((ts, f"SAID   {text[:400]}"))
    for ts, text in sorted(merged):
        add(f"{clock(ts)}  {text}")
    add("```")
    add("")

    # ---- what they said on the forms -------------------------------------
    add("## What they told us")
    add("")
    for r in sorted(responses, key=lambda x: x["_id"]):
        values = r.get("values") or {}
        if not values:
            continue
        add(f"### {r['_id']}")
        for k, v in sorted(values.items()):
            if isinstance(v, str) and len(v) > 90:
                add(f"- **{k}**:")
                add(f"  > {v}")
            else:
                add(f"- **{k}**: {v}")
        add("")

    if notes:
        add("## Interview notes")
        add("")
        for n in sorted(notes, key=lambda x: x.get("ts") or 0):
            add(f"- **{n.get('probeId')}** ({clock(n.get('ts'))}): {n.get('text')}")
        add("")

    out = "\n".join(L)
    if args.out:
        Path(args.out).write_text(out)
        print(f"wrote {args.out} ({len(out.splitlines())} lines)")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
