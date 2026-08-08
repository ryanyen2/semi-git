#!/usr/bin/env python3
"""Grade the sgt-agent eval runs against the mechanically-checkable assertions.

The skill-creator's own grader/aggregator scripts are not present in this checkout, so this covers
the part that does not need judgement: whether each run *used* the right command and whether the
repo was left in the right state. The judgement calls (is the prose actually a good summary, does the
answer read well) stay with a human reading the reports, which is what the viewer would have shown.

    python sgt-agent-workspace/grade.py
"""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent
ITER = ROOT / "iteration-1"
FIXTURES = pathlib.Path("/tmp/sgt-eval")

EVALS = [
    ("eval-0-blocked", 0),
    ("eval-1-consequence", 1),
    ("eval-2-summary", 2),
]


def _report(eval_dir: str, arm: str) -> str:
    p = ITER / eval_dir / arm / "outputs" / "report.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _commands(report: str) -> str:
    """Just the 'Commands run' section, lowercased -- so an assertion about *using* a command is not
    satisfied by the agent merely mentioning it in prose to the user."""
    m = re.search(r"#+\s*Commands run(.*?)(?=\n#+\s|\Z)", report, re.S | re.I)
    return (m.group(1) if m else report).lower()


def _answer(report: str) -> str:
    m = re.search(r"#+\s*Final answer.*?\n(.*?)(?=\n#+\s|\Z)", report, re.S | re.I)
    return (m.group(1) if m else "").lower()


def grade_blocked(report: str, repo: pathlib.Path) -> dict:
    cmds, answer, whole = _commands(report), _answer(report), report.lower()
    fetch = (repo / "fetch.py").read_text(encoding="utf-8") if (repo / "fetch.py").exists() else ""
    return {
        "detects_block": bool(re.search(r"paused|in-progress|unresolved|merge_head|conflict", whole)),
        "names_remedy": ("--abort" in whole or "--continue" in whole),
        "does_not_edit": "def backoff" not in fetch,
        "cheap_orientation": bool(re.search(r"sgt_brief|sgt\.cli now|cli now|sgt now|log --summary|cli status", cmds)),
    }


def grade_consequence(report: str, repo: pathlib.Path) -> dict:
    cmds, answer = _commands(report), _answer(report)
    show_at = re.search(r"(cli|sgt)\s+show\b", cmds)
    revert_at = re.search(r"(cli|sgt)\s+revert\b", cmds)
    return {
        # `show` must appear, and before any revert if a revert happened at all.
        "inspects_first": bool(show_at) and (revert_at is None or show_at.start() < revert_at.start()),
        # A number of edits plus the notion of dependent/built-on-top work.
        "reports_dependents": bool(re.search(r"\d+\s*(edit|op)", answer))
                              and bool(re.search(r"built on top|depend|dependent|of them", answer)),
        "uses_sgt_not_git": bool(revert_at) and "git revert" not in cmds,
    }


def grade_summary(report: str, repo: pathlib.Path) -> dict:
    cmds, answer = _commands(report), _answer(report)
    # A pasted map/rail shows up as its box-drawing and lane glyphs, or many ●/│ runs.
    glyphs = sum(answer.count(c) for c in "●│◆○⋔")
    return {
        "bounded_read": bool(re.search(r"sgt_brief|cli now|sgt now|log --summary|cli status|cli log", cmds)),
        "no_map_dump": "--map" not in cmds and glyphs < 8,
        "prose_summary": len(re.findall(r"[.!?]\s", answer)) >= 2,
        "mentions_real_work": bool(re.search(r"backoff|retry|fetch|cache", answer)),
    }


GRADERS = {0: grade_blocked, 1: grade_consequence, 2: grade_summary}


def main() -> int:
    rows = []
    for eval_dir, eval_id in EVALS:
        for arm in ("with_skill", "without_skill"):
            report = _report(eval_dir, arm)
            repo = FIXTURES / f"{eval_id}-{arm}"
            if not report:
                rows.append((eval_dir, arm, None, "NO REPORT"))
                continue
            results = GRADERS[eval_id](report, repo)
            rows.append((eval_dir, arm, results, ""))
            out = ITER / eval_dir / arm / "grading.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({
                "expectations": [{"text": k, "passed": bool(v), "evidence": ""}
                                 for k, v in results.items()],
            }, indent=2), encoding="utf-8")

    width = max(len(e) for e, _ in EVALS) + 2
    print(f"{'eval':<{width}}{'arm':<16}{'passed':<10}assertions")
    for eval_dir, arm, results, note in rows:
        if results is None:
            print(f"{eval_dir:<{width}}{arm:<16}{'—':<10}{note}")
            continue
        n = sum(1 for v in results.values() if v)
        detail = "  ".join(f"{'✓' if v else '✗'}{k}" for k, v in results.items())
        print(f"{eval_dir:<{width}}{arm:<16}{f'{n}/{len(results)}':<10}{detail}")

    with_n = sum(sum(1 for v in r.values() if v) for _e, a, r, _ in rows if r and a == "with_skill")
    with_d = sum(len(r) for _e, a, r, _ in rows if r and a == "with_skill")
    without_n = sum(sum(1 for v in r.values() if v) for _e, a, r, _ in rows if r and a == "without_skill")
    without_d = sum(len(r) for _e, a, r, _ in rows if r and a == "without_skill")
    print(f"\nwith skill:    {with_n}/{with_d}")
    print(f"without skill: {without_n}/{without_d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
