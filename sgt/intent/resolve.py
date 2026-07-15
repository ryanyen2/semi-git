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


def _user_symbols(op: dict) -> list[str]:
    """An op's footprint symbols a user could actually mean, i.e. dropping sgt's synthetic
    `__anchor__`/`__residue__` pseudo-symbols (`sgt.core.op._symbol_kind` == anchor/residue):
    they're byte-fidelity/ordering internals, never a natural-language revert/restore target."""
    from sgt.core.op import _symbol_kind

    return sorted(
        f["symbol"] for f in op["footprint"]
        if _symbol_kind(f["symbol"]) not in ("anchor", "residue")
    )


def _context(repo: Path, verb: str) -> str:
    """Compact, no-file-bytes context (mirrors `sgt.repair.context`): known features plus the op/
    symbol vocabulary a candidate `ref` can be drawn from -- **verb-aware**, because `revert` and
    `restore` target disjoint pools. `revert` removes something live, so its vocabulary is the ops
    in the current ideal. `restore` re-adds something *removed*, so its vocabulary is the ops in
    HEAD's provenance that are no longer in the ideal (the same set `plan_restore` resolves
    against) -- listing the live frontier there would only ever yield no-op candidates. Synthetic
    anchor/residue symbols are filtered from both (`_user_symbols`)."""
    from sgt.api import map_view, oplog_view
    from sgt.core.lens import current_ideal, ideal_for_ref

    tree = map_view(repo)
    features = "\n".join(f"{n['id']}: {n['label']}" for n in tree["nodes"] if n["kind"] == "feature")

    ideal_ids = current_ideal(repo).op_ids
    all_ops = oplog_view(repo)["ops"]
    if verb == "restore":
        restorable = ideal_for_ref(repo, "HEAD").op_ids
        pool = [op for op in all_ops if op["id"] in restorable and op["id"] not in ideal_ids]
        op_header = "Removed ops that `restore` can bring back (id | intent | symbols)"
        sym_header = "Removed symbols that can be restored (file::name)"
    else:
        pool = [op for op in all_ops if op["id"] in ideal_ids]
        op_header = "Live ops in the current ideal (id | intent | symbols)"
        sym_header = "Live symbols at the frontier (file::name)"

    pool = [op for op in pool if _user_symbols(op)][:_MAX_OPS]
    op_lines = "\n".join(
        f"{op['id'][:8]} | {op['intent'] or ''} | " + ", ".join(_user_symbols(op)) for op in pool
    )
    symbols = "\n".join(sorted({s for op in pool for s in _user_symbols(op)}))

    return (
        f"Known features (id: label):\n{features or '(none -- no feature tree built yet)'}\n\n"
        f"{op_header}:\n{op_lines or '(none)'}\n\n"
        f"{sym_header}:\n{symbols or '(none)'}\n"
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
            f"{_context(repo, verb or 'revert')}\n"
            "Propose up to 5 ranked candidate targets that could be what they mean. Each "
            "candidate's ref must appear verbatim above -- an op id (the first 8 hex chars shown "
            "are enough), a `file::symbol` name, or a feature id -- never invent one. kind is "
            '"op", "symbol", or "feature" matching which list it came from. rationale is one line '
            "explaining the match.\n"
            "Only include a candidate you are genuinely confident the user means. If the query "
            "does not plausibly refer to anything listed above (e.g. it names a concept that "
            "isn't in this codebase), return an empty candidates list rather than guessing at "
            "unrelated targets -- a wrong guess here can delete real work."
        )
        client = get_client(repo)
        r = client.responses.parse(
            model=MODEL, input=prompt, text_format=IntentResolution, reasoning={"effort": EFFORT},
        )
        return r.output_parsed
    except Exception:
        return None
