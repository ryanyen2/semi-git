"""The repair controller (plan U5): drives a backend's proposals through Tier-0, then the real
`stage -> oracle -> land` gate, without ever bypassing it.

Two nested loops, cheapest check first (SWE-bench's dual-gate structure): per hollow, a pure,
free Tier-0 loop (build context -> `backend.propose` -> static verification, up to
`max_attempts`); once every hollow in the draft has a Tier-0-passing image, one real oracle round
(`stage` once, run the oracle, `land` on a pass or `unstage` and deterministically re-draft on a
fail, up to `max_oracle_rounds`). A red final oracle verdict is a real failure, not a silently
overridden one -- `land`'s gate is never bypassed here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from sgt.core import oracle, rewrite
from sgt.core.op import Attribution
from sgt.core.store import Store
from sgt.repair import context, verify
from sgt.repair.backends import RepairBackend


@dataclass(frozen=True)
class RepairResult:
    ok: bool
    sha: str | None = None
    attempts: int = 0
    oracle_rounds: int = 0
    message: str = ""
    cost_line: str = ""


class StuckDetector:
    """Stops a hollow's Tier-0 loop early once a backend repeats an image it already tried (and
    that already failed) -- no point spending the remaining attempts on the same rejection."""

    def __init__(self) -> None:
        self._seen: set[bytes] = set()

    def is_repeat(self, image: bytes) -> bool:
        if image in self._seen:
            return True
        self._seen.add(image)
        return False


def _tail_of(record: dict | None) -> str:
    for name, tier in (record or {}).get("tiers", {}).items():
        if tier.get("status") == "fail":
            return f"oracle tier {name!r} failed:\n{tier.get('output_tail', '')}"
    return "oracle verdict is not passing"


def _cost_line(backend: RepairBackend) -> str:
    cost = getattr(backend, "cost_line", None)
    return cost() if callable(cost) else ""


def repair(
    repo: str | Path, draft: rewrite.RewriteDraft, backend: RepairBackend, *,
    max_attempts: int = 4, max_oracle_rounds: int = 2, plan: str | None = None,
) -> RepairResult:
    """Fulfills every hollow in `draft` via `backend`, lands the result once the oracle passes.
    `ok=False` always leaves the working tree clean (nothing staged) -- either Tier-0 exhausted
    a hollow's attempt budget before ever calling `stage`, or every oracle round came back red
    and the last round's `unstage` already ran."""
    repo = Path(repo)
    store = Store(repo)
    total_attempts = 0
    feedback_by_hollow: dict[str, str | None] = {h: None for h in draft.hollow_ids}

    for oracle_round in range(1, max_oracle_rounds + 1):
        winners: dict[str, bytes] = {}
        resolved_ids: list[str] = []

        for hollow_id in draft.hollow_ids:
            detector = StuckDetector()
            feedback = feedback_by_hollow.get(hollow_id)
            residual = ""
            resolved = False
            for attempt in range(1, max_attempts + 1):
                total_attempts += 1
                request = context.build_request(repo, draft, hollow_id, attempt=attempt, feedback=feedback)
                proposal = backend.propose(request)
                image = proposal.image.encode("utf-8")
                if detector.is_repeat(image):
                    residual = f"{request.symbol}: backend repeated a rejected image, giving up"
                    break
                sub_draft = replace(draft, hollow_ids=(*resolved_ids, hollow_id))
                verdict = verify.tier0(repo, sub_draft, {**winners, hollow_id: image})
                if verdict.ok:
                    winners[hollow_id] = image
                    resolved_ids.append(hollow_id)
                    resolved = True
                    break
                feedback = verdict.residual
                residual = verdict.residual
            if not resolved:
                return RepairResult(
                    ok=False, attempts=total_attempts, oracle_rounds=oracle_round - 1,
                    message=f"{hollow_id[:12]}: {residual or 'no proposal passed Tier-0'}",
                    cost_line=_cost_line(backend),
                )

        _, fulfilled = rewrite.build_candidate(repo, draft, images=winners)
        candidate = rewrite.stage(repo, draft, images=winners)
        oracle.run(repo, ideal=candidate)
        verdict_record = oracle.verdict_for(repo, candidate)
        if oracle.overall_status(verdict_record) == "pass":
            sha = rewrite.land(repo, message=f"sgt repair {draft.target}")
            for op in fulfilled.values():
                # `rewrite.land` deliberately doesn't re-mine (unlike `session.land`), so `op.provenance`
                # never picks up `sha` on its own -- and `Store._serialize` only persists an `Attribution`
                # entry for a sha present in `provenance`. Record the witness before attributing it.
                store.add(replace(op, provenance=(sha,)))
                store.attribute(op.id, (Attribution(sha=sha, agent="integration", plan=plan),))
            return RepairResult(
                ok=True, sha=sha, attempts=total_attempts, oracle_rounds=oracle_round,
                cost_line=_cost_line(backend),
            )

        tail = _tail_of(verdict_record)
        rewrite.unstage(repo)
        if oracle_round < max_oracle_rounds:
            draft = rewrite.revert_keep_dependents(repo, draft.target)
            feedback_by_hollow = {h: tail for h in draft.hollow_ids}

    return RepairResult(
        ok=False, attempts=total_attempts, oracle_rounds=max_oracle_rounds,
        message="oracle verdict stayed red after all rounds", cost_line=_cost_line(backend),
    )
