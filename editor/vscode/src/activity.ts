// Live agent presence, tailed from the Claude Code session transcript. Claude Code writes each
// session as JSONL to ~/.claude/projects/<encoded-cwd>/<session>.jsonl. We tail the newest file
// from its end (no backlog replay) and distill each appended line into a minimal ActivityEvent —
// just enough to show "the agent is editing X / thinking about Y" so the semantic graph never
// looks stale while work is happening. This is ephemeral telemetry, deliberately NOT part of
// sgt.api (see types.ts). Everything is best-effort: a missing dir or an unparseable line is a
// silent no-op, never an error to the user.

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { ActivityEvent } from "./types";

const POLL_MS = 1000;
const THOUGHT_MAX = 110;

export class ClaudeActivityWatcher {
  private timer: NodeJS.Timeout | undefined;
  private dir: string;
  private file: string | undefined; // currently-tailed transcript
  private offset = 0; // byte offset we've consumed up to
  private seq = 0;
  private lastThoughtAt = 0; // throttle thinking lines (they're chatty)

  constructor(
    workspaceRoot: string,
    private onEvents: (events: ActivityEvent[]) => void
  ) {
    // Claude encodes the project cwd by replacing "/" and "." with "-".
    const enc = workspaceRoot.replace(/[/.]/g, "-");
    this.dir = path.join(os.homedir(), ".claude", "projects", enc);
  }

  start(): void {
    if (this.timer) {
      return;
    }
    this.tick(); // attach immediately, then poll
    this.timer = setInterval(() => this.tick(), POLL_MS);
  }

  private tick(): void {
    try {
      const next = this.newestTranscript();
      if (!next) {
        return;
      }
      if (next !== this.file) {
        // New session (or first attach): tail from the end so we stream live, not replay history.
        this.file = next;
        this.offset = safeSize(next);
        return;
      }
      const size = safeSize(next);
      if (size <= this.offset) {
        if (size < this.offset) {
          this.offset = size; // file truncated/rotated in place — resync
        }
        return;
      }
      const fd = fs.openSync(next, "r");
      try {
        const len = size - this.offset;
        const buf = Buffer.alloc(len);
        fs.readSync(fd, buf, 0, len, this.offset);
        this.offset = size;
        this.consume(buf.toString("utf8"));
      } finally {
        fs.closeSync(fd);
      }
    } catch {
      // dir gone, permission, mid-write race — ignore this tick.
    }
  }

  /** Newest *.jsonl by mtime, or undefined if the project dir has no transcripts yet. */
  private newestTranscript(): string | undefined {
    let names: string[];
    try {
      names = fs.readdirSync(this.dir).filter((n) => n.endsWith(".jsonl"));
    } catch {
      return undefined;
    }
    let best: string | undefined;
    let bestMtime = -1;
    for (const n of names) {
      const full = path.join(this.dir, n);
      try {
        const m = fs.statSync(full).mtimeMs;
        if (m > bestMtime) {
          bestMtime = m;
          best = full;
        }
      } catch {
        // disappeared between readdir and stat
      }
    }
    return best;
  }

  private consume(chunk: string): void {
    const events: ActivityEvent[] = [];
    for (const line of chunk.split("\n")) {
      const t = line.trim();
      if (!t) {
        continue;
      }
      let obj: any;
      try {
        obj = JSON.parse(t);
      } catch {
        continue; // partial trailing line — next tick re-reads from a fresh offset boundary
      }
      const content = obj?.message?.content;
      if (!Array.isArray(content)) {
        continue;
      }
      for (const block of content) {
        const ev = this.blockToEvent(block);
        if (ev) {
          events.push(ev);
        }
      }
    }
    if (events.length) {
      this.onEvents(events);
    }
  }

  private blockToEvent(block: any): ActivityEvent | null {
    if (!block || typeof block !== "object") {
      return null;
    }
    if (block.type === "tool_use") {
      return { kind: "tool", name: String(block.name || "tool"), target: toolTarget(block.input), seq: ++this.seq };
    }
    if (block.type === "thinking" || block.type === "text") {
      const raw = block.type === "thinking" ? block.thinking : block.text;
      const text = firstSentence(String(raw || ""));
      if (!text) {
        return null;
      }
      // Throttle thoughts so a long reasoning burst doesn't flood the feed.
      const now = Date.now();
      if (now - this.lastThoughtAt < 1500) {
        return null;
      }
      this.lastThoughtAt = now;
      return { kind: "thought", text, seq: ++this.seq };
    }
    return null;
  }

  dispose(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = undefined;
    }
  }
}

function safeSize(file: string): number {
  try {
    return fs.statSync(file).size;
  } catch {
    return 0;
  }
}

/** A human target for a tool call: the edited file's basename, else a command/pattern token. */
function toolTarget(input: any): string | undefined {
  if (!input || typeof input !== "object") {
    return undefined;
  }
  if (typeof input.file_path === "string") {
    return path.basename(input.file_path);
  }
  if (typeof input.notebook_path === "string") {
    return path.basename(input.notebook_path);
  }
  if (typeof input.command === "string") {
    return input.command.trim().split(/\s+/)[0];
  }
  if (typeof input.pattern === "string") {
    return input.pattern;
  }
  if (typeof input.path === "string") {
    return path.basename(input.path);
  }
  return undefined;
}

function firstSentence(s: string): string {
  const clean = s.replace(/\s+/g, " ").trim();
  if (!clean) {
    return "";
  }
  const cut = clean.slice(0, THOUGHT_MAX);
  return cut.length < clean.length ? cut.replace(/\s\S*$/, "") + "…" : cut;
}
