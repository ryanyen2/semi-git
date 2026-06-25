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


class DecisionStatus(str, Enum):
    """The lifecycle phase of a decision on the time axis.

    ``PLANNED`` is a tentative capability with no effects yet (a graph node the coding
    agent has not implemented). ``LANDED`` has real effects/commits but is not the
    in-force composition for its lane. ``IN_FORCE`` is a landed decision the frontier
    currently selects — what a "now" view materializes. Status is never hue; surfaces
    render it as a glyph + dim.
    """

    PLANNED = "planned"
    LANDED = "landed"
    IN_FORCE = "in_force"


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
    made); ``slug`` is a short ~5-word human handle for it (the row title); ``context``
    (why/preconditions) and ``consequence`` (what is now guaranteed) are authored from
    deliberation. All but ``decision`` may be ``None`` for a bare log-recovered decision."""

    decision: str
    slug: str | None = None
    context: str | None = None
    consequence: str | None = None

    def to_dict(self) -> dict:
        return {"context": self.context, "decision": self.decision,
                "consequence": self.consequence, "slug": self.slug}

    @classmethod
    def from_dict(cls, d: dict) -> "Intent":
        return cls(decision=d["decision"], slug=d.get("slug"),
                   context=d.get("context"), consequence=d.get("consequence"))


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
    # PLANNED (no effects) or LANDED (has effects). A landed decision is upgraded to
    # IN_FORCE by the projection (sgt.api) when the frontier selects it; the store only
    # ever sets PLANNED or LANDED, since "in force" is a frontier property, not a log one.
    status: DecisionStatus = DecisionStatus.LANDED

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "feature": self.feature,
            "landing": self.landing,
            "status": self.status.value,
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
            status=DecisionStatus(d.get("status", DecisionStatus.LANDED.value)),
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
        """The default frontier: each lane's latest-landing *landed* decision.

        Planned decisions never enter the frontier — they have no effects, so nothing
        is materialized for them. A planned-only workspace therefore has an empty
        frontier (and ``materialize`` sees no in-force decision to draw effects from).
        """
        best: dict[str, Decision] = {}
        for d in decisions:
            if d.status is DecisionStatus.PLANNED:
                continue
            cur = best.get(d.feature)
            if cur is None or d.landing > cur.landing:
                best[d.feature] = d
        return cls(selection={feat: dec.id for feat, dec in best.items()})
