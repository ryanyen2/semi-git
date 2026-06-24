"""Decision-layer data model — pure dataclasses, no I/O.

The shapes here are what every surface ultimately renders, so they stay small and JSON
round-trippable. A ``Decision``'s *structural* fields (``footprint``, ``commits``,
``landing``, ``lifecycle``) are recovered from the log by ``sgt.decisions.store``; its
*authored* fields (``intent.context`` / ``intent.consequence`` / ``alternatives``) come
from witnessable deliberation and default empty until the LLM-glue path fills them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LifecycleKind(str, Enum):
    """How a decision relates to the one it descends from — the only *intrinsic* edge.

    ``INTRODUCE`` starts a feature lane. ``REVISE`` is a later take on the same lane (the
    node accreting effects across checkpoints, or a fix node revising its parent).
    ``FORK`` starts a *new* lane as an alternative to another decision (revoke-by-fork).
    Dependency (``builds-on``) is not here — it is derived from the entity graph.
    """

    INTRODUCE = "introduce"
    REVISE = "revise"
    FORK = "fork"


@dataclass
class Intent:
    """The ADR-style rationale of a decision. ``decision`` is always present (the choice
    made); ``context`` (why/preconditions) and ``consequence`` (what is now guaranteed)
    are authored from deliberation and may be ``None`` for log-recovered decisions."""

    decision: str
    context: str | None = None
    consequence: str | None = None

    def to_dict(self) -> dict:
        return {"context": self.context, "decision": self.decision, "consequence": self.consequence}

    @classmethod
    def from_dict(cls, d: dict) -> "Intent":
        return cls(decision=d["decision"], context=d.get("context"), consequence=d.get("consequence"))


@dataclass
class Alternative:
    """A road not taken: the option weighed and why it lost, with provenance.

    ``source`` records where the rationale came from (``transcript`` / ``plan`` / ``distilled``)
    and ``confidence`` marks distilled rationale as low so a UI never presents a fabricated
    alternative as fact (R3).
    """

    option: str
    why_rejected: str
    source: str = "distilled"
    confidence: str = "low"

    def to_dict(self) -> dict:
        return {
            "option": self.option,
            "why_rejected": self.why_rejected,
            "source": self.source,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Alternative":
        return cls(
            option=d["option"],
            why_rejected=d.get("why_rejected", ""),
            source=d.get("source", "distilled"),
            confidence=d.get("confidence", "low"),
        )


@dataclass
class Decision:
    """One decision = the effects of one feature that landed at one checkpoint.

    ``id`` is ``f"{node_id}@{landing}"``. ``feature`` is the *lane* id (the revise-root
    node), so a fix that revises a feature shares the feature's lane. ``footprint`` is the
    set of entity keys (``file::target``) the decision's effects touched — the join key for
    derived ``builds-on`` against the entity graph.
    """

    id: str
    node_id: str
    feature: str
    landing: int
    intent: Intent
    footprint: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    alternatives: list[Alternative] = field(default_factory=list)
    lifecycle_kind: LifecycleKind = LifecycleKind.INTRODUCE
    lifecycle_of: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "feature": self.feature,
            "landing": self.landing,
            "intent": self.intent.to_dict(),
            "footprint": list(self.footprint),
            "commits": list(self.commits),
            "alternatives": [a.to_dict() for a in self.alternatives],
            "lifecycle": {"kind": self.lifecycle_kind.value, "of": self.lifecycle_of},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Decision":
        lc = d.get("lifecycle", {})
        return cls(
            id=d["id"],
            node_id=d["node_id"],
            feature=d["feature"],
            landing=d["landing"],
            intent=Intent.from_dict(d["intent"]),
            footprint=list(d.get("footprint", [])),
            commits=list(d.get("commits", [])),
            alternatives=[Alternative.from_dict(a) for a in d.get("alternatives", [])],
            lifecycle_kind=LifecycleKind(lc.get("kind", LifecycleKind.INTRODUCE.value)),
            lifecycle_of=lc.get("of"),
        )


@dataclass
class Frontier:
    """HEAD as a composition: one in-force decision per feature lane.

    ``selection`` maps lane (feature id) -> decision id. The working tree is materialized
    from these decisions. The default selection is each lane's tip (latest landing); pinning
    a lane to an earlier decision is what lets feature-A@v3 coexist with feature-B@latest.
    """

    selection: dict[str, str] = field(default_factory=dict)

    def in_force(self) -> set[str]:
        return set(self.selection.values())

    def to_dict(self) -> dict:
        return {"selection": dict(self.selection)}

    @classmethod
    def from_dict(cls, d: dict) -> "Frontier":
        return cls(selection=dict(d.get("selection", {})))

    @classmethod
    def tip_of(cls, decisions: list[Decision]) -> "Frontier":
        """The default frontier: each lane's latest-landing decision."""
        best: dict[str, Decision] = {}
        for d in decisions:
            cur = best.get(d.feature)
            if cur is None or d.landing > cur.landing:
                best[d.feature] = d
        return cls(selection={feat: dec.id for feat, dec in best.items()})
