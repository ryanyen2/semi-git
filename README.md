# semi-git (`sgt`)

Version a codebase by its **features and concepts**, not its diffs.

`sgt` maintains a living semantic DAG (`.sgt/`) over an ordinary git repo. A developer
works through a freeform prompt stream plus explicit graph verbs; semi-git distills that
into feature/concept nodes, delegates code-writing to an external coding agent, and runs
every mutation through the **EICO confluence gate** so nothing lands unless it commutes
and preserves the codebase's invariants.

See:
- `docs/ideation/2026-06-17-semi-git-ideation.md` — where the idea came from
- `docs/brainstorms/2026-06-17-semi-git-requirements.md` — what it is (requirements)
- `docs/plans/2026-06-17-001-feat-semi-git-core-plan.md` — how it's built (plan)

## Development

Requires [`uv`](https://docs.astral.sh/uv/).

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run pytest
```

## Status

Early v1. Building the semantic core (Phase A): `.sgt` store + graph model + git binding.
