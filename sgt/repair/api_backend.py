"""The default `RepairBackend`: a direct OpenAI call (plan U4), copying `sgt.lens.label`'s pattern
(client/cache/token-accounting) with one deliberate divergence -- **no offline fallback**. A
label that's slightly off just gets renamed later; a repair proposal that's plausible-but-wrong
silently ships broken code past a Tier-0 pass it shouldn't have gotten. No API key, or any error
from the call, propagates rather than degrading to a guess -- `sgt.repair.backends.EchoBackend`
(or a test's `FakeBackend`) is the deterministic offline substitute, not a fallback baked in here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from sgt import state
from sgt.config import get_client
from sgt.repair.backends import RepairBackend, RepairProposal, RepairRequest

MODEL = "gpt-5.4-mini"
EFFORT = "low"


def _key(request: RepairRequest) -> str:
    image_hash = hashlib.sha1(request.current_image.encode("utf-8")).hexdigest()[:12]
    feedback_hash = hashlib.sha1((request.feedback or "").encode("utf-8")).hexdigest()[:12]
    payload = "\x00".join((
        request.symbol, request.removed_symbol, image_hash, str(request.attempt), feedback_hash,
    ))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _prompt(request: RepairRequest) -> str:
    lines = [
        "Rewrite one code symbol so it no longer depends on a symbol that was removed, in a "
        "semantic version-control tool's automated repair loop.",
        f"Symbol to rewrite: {request.symbol}",
        "Current bytes:",
        request.current_image,
        f"Removed symbol: {request.removed_symbol}",
    ]
    if request.removed_intent:
        lines.append(f"Why it was removed: {request.removed_intent}")
    if request.removed_signature:
        lines.append(f"Removed symbol's signature: {request.removed_signature}")
    if request.neighbors:
        lines.append("Other symbols still available (signatures only):")
        lines.extend(f"  {n}" for n in request.neighbors)
    if request.feedback:
        lines.append(f"Attempt {request.attempt} was rejected: {request.feedback}")
    lines.append(
        "Return the complete new bytes for the symbol (same name/signature, same language), with "
        "no remaining reference to the removed symbol, plus a one-line rationale."
    )
    return "\n".join(lines)


class ApiBackend(RepairBackend):
    """Cached by `(symbol, removed_symbol, current_image, attempt, feedback)` -- a hollow retried
    with unchanged context (e.g. a StuckDetector re-check) never re-pays for the same call."""

    def __init__(self, repo: str | Path = ".") -> None:
        self._repo = repo
        self._client = None
        self.cache: dict = state.load_json(repo, "repair_cache", default={})
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0

    @property
    def client(self):
        if self._client is None:
            self._client = get_client(self._repo)
        return self._client

    def propose(self, request: RepairRequest) -> RepairProposal:
        key = _key(request)
        cached = self.cache.get(key)
        if cached is not None:
            return RepairProposal(**cached)
        r = self.client.responses.parse(
            model=MODEL, input=_prompt(request), text_format=RepairProposal,
            reasoning={"effort": EFFORT},
        )
        self.calls += 1
        self.tokens_in += r.usage.input_tokens
        self.tokens_out += r.usage.output_tokens
        proposal = r.output_parsed
        self.cache[key] = proposal.model_dump()
        return proposal

    def save(self) -> None:
        state.save_json(self._repo, "repair_cache", self.cache)

    def cost_line(self) -> str:
        est = self.tokens_in / 1e6 * 0.25 + self.tokens_out / 1e6 * 2.0  # ~ballpark $/Mtok
        return (
            f"repair backend: {self.calls} live calls, "
            f"{self.tokens_in} in + {self.tokens_out} out tokens (~${est:.4f}); "
            f"{len(self.cache)} cached"
        )
