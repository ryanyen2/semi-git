"""The pluggable "propose a fix" seam (plan U1).

A `RepairBackend` proposes new bytes for one hollow's symbol, given the same compressed context a
human fulfilling it by hand would want (`context.build_request`). This ships with `EchoBackend`
(a no-op backend that only exercises the contract) and `sgt.repair.api_backend.ApiBackend` (the
default, OpenAI-backed). A future `SandboxBackend` -- handing the hollow off to a user's own
coding agent running in an isolated session worktree (`sgt.core.session`), then reading the
agent's edit back via `rewrite._entity_bytes_from_tree` -- is a documented extension point, not
built here: it needs nothing from `RepairBackend` beyond implementing `propose`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class RepairRequest(BaseModel):
    """Compressed, per-hollow context for one repair attempt (`context.build_request`)."""

    symbol: str  # the hollow's own symbol id (file::name) -- what the proposal must rewrite
    current_image: str  # sym's current bytes (working tree, or its frontier op's image)
    removed_symbol: str  # the symbol `sym` must stop depending on
    removed_intent: str | None  # the removed op's advisory intent, if any
    removed_signature: str  # first line of the removed symbol's last-live image
    neighbors: list[str] = []  # one-hop reference neighbors of `sym`, signature lines only
    attempt: int = 1  # 1-based; > 1 means `feedback` carries a prior attempt's rejection reason
    feedback: str | None = None  # Tier-0 residual or oracle output_tail from the prior attempt


class RepairProposal(BaseModel):
    """A backend's proposed fix: `image` is `symbol`'s complete new bytes, not a diff -- `stage`'s
    existing unit is a whole entity image, so a proposal needs no diff-apply step of its own."""

    image: str
    rationale: str


class RepairBackend(ABC):
    """Proposes new bytes for one hollow. Implementations may cache, call an LLM, or shell out --
    `loop.py` only ever calls `propose` and treats every backend identically."""

    @abstractmethod
    def propose(self, request: RepairRequest) -> RepairProposal:
        raise NotImplementedError


class EchoBackend(RepairBackend):
    """Trivial backend that returns `current_image` unchanged -- exercises the `RepairBackend`
    contract (and the loop around it) without an API key or any real rewrite. Useless for an
    actual repair: an unchanged image still calls the removed symbol, so Tier-0 rejects it every
    time -- exactly what makes it useful for testing the reject-and-retry path deterministically."""

    def propose(self, request: RepairRequest) -> RepairProposal:
        return RepairProposal(image=request.current_image, rationale="echo: no change")
