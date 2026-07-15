"""NL target resolution (plan U8/U10/U13's fallback ladder, last rung): given a free-form
query, ask an LLM which live refs it probably means. Mirrors `sgt.loop.plan._llm_decompose`'s
call/fallback shape exactly -- same model/effort tier, same "ground the prompt in the repo's
own ids, never a name from thin air" instruction, same `None`-on-any-exception contract so a
caller degrades cleanly with no key, no network, or a malformed response.

Deliberately no cache (unlike `sgt.repair.api_backend.ApiBackend`): free-form queries have a
low repeat-hit rate, so the cache would mostly hold dead weight.

**Discipline:** this module only ever names candidate refs. It never computes an op-set delta
and never touches the ideal algebra -- the caller re-runs its own `plan_*` on each candidate to
get a truthful preview, which also silently drops any hallucinated or no-longer-live ref.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from sgt.config import get_client

MODEL = "gpt-5.4-mini"
EFFORT = "low"

_MAX_OPS = 200  # keeps the prompt compact on a large ideal (plan U2 "localize tightly")


class Candidate(BaseModel):
    ref: str  # must appear verbatim in the prompt's context -- an op-id, `file::symbol`, or feature id
    kind: str  # "op" | "symbol" | "feature"
    rationale: str


class IntentResolution(BaseModel):
    candidates: list[Candidate]  # ranked, <=5


def _context(repo: Path) -> str:
    """Compact, no-file-bytes context (mirrors `sgt.repair.context`): known features, the live
    ops in the current ideal, and the frontier's live symbols -- the same three vocabularies a
    candidate `ref` can be drawn from."""
    from sgt.api import map_view, oplog_view, state_view
    from sgt.core.lens import current_ideal

    tree = map_view(repo)
    features = "\n".join(f"{n['id']}: {n['label']}" for n in tree["nodes"] if n["kind"] == "feature")

    ideal_ids = current_ideal(repo).op_ids
    live_ops = [op for op in oplog_view(repo)["ops"] if op["id"] in ideal_ids][:_MAX_OPS]
    op_lines = "\n".join(
        f"{op['id'][:8]} | {op['intent'] or ''} | " + ", ".join(sorted(f["symbol"] for f in op["footprint"]))
        for op in live_ops
    )

    symbols = "\n".join(sorted(state_view(repo)["frontier"]))

    return (
        f"Known features (id: label):\n{features or '(none -- no feature tree built yet)'}\n\n"
        f"Live ops in the current ideal (id | intent | symbols):\n{op_lines or '(none)'}\n\n"
        f"Live symbols at the frontier (file::name):\n{symbols or '(none)'}\n"
    )


def resolve_intent(repo: str | Path, query: str, *, verb: str | None = None) -> IntentResolution | None:
    """Ask the LLM which live refs `query` probably names, grounded in `repo`'s own op/feature/
    symbol ids. Any failure (no key, no network, malformed response) returns `None` -- the
    caller falls back to a clear "could not resolve" message rather than guessing."""
    repo = Path(repo)
    try:
        for_verb = f" for `sgt {verb}`" if verb else ""
        prompt = (
            f"A user wants to target something in their codebase's tracked history{for_verb} by "
            "describing it in plain language rather than naming it exactly.\n"
            f"Query: {query!r}\n\n"
            f"{_context(repo)}\n"
            "Propose up to 5 ranked candidate targets that could be what they mean. Each "
            "candidate's ref must appear verbatim above -- an op id (the first 8 hex chars shown "
            "are enough), a `file::symbol` name, or a feature id -- never invent one. kind is "
            '"op", "symbol", or "feature" matching which list it came from. rationale is one line '
            "explaining the match."
        )
        client = get_client(repo)
        r = client.responses.parse(
            model=MODEL, input=prompt, text_format=IntentResolution, reasoning={"effort": EFFORT},
        )
        return r.output_parsed
    except Exception:
        return None
