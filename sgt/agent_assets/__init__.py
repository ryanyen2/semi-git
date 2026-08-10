"""Files `sgt init --agent` copies into a repo to wire up a coding agent.

`skills/` holds the Claude Code skills (`sgt-agent`, `sgt-plan`, `sgt-workflow`). They live here,
inside the package, rather than in this repo's own `.claude/skills/` so that a plain
`uv tool install semi-git` can install them into any repo without a checkout present. This repo
gets its own copy the same way everyone else does, by running `sgt init --agent`.
"""

from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"
