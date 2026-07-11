"""The proposal object: a reviewable base+Δ over the op DAG, landable and GitHub-renderable (plan
U24, C10).

A proposal captures a unit of review: a *base frontier* (some ref's committed ideal at create time),
the *Δ op-set* this session adds on top of it, the *feature ids* Δ touches, a link to the published
oracle *claim* for base∪Δ, and *provenance*. It is creatable, checkable for staleness by re-union
against the (possibly moved) base, landable via the U23 CAS branch advance, and renderable as a
GitHub PR body -- a *pure projection* of the view, with no GitHub API dependency in this unit (`gh`
invocation is follow-on porcelain).

Like a claim (D8), a proposal is a committed, immutable review object: one file per id under
`.sgt/proposals/`, content-addressed by base+Δ, travelling on sync as a file-level G-Set
(`materialize._union_proposals`). Staleness is never *stored* -- it is *computed* on demand against
the current base (`status`), so a proposal that was clean at create time correctly reports `fork`
once the base reworks a symbol Δ also touches (divergence-as-state, D5), and reports `clean-reunion`
once the base merely advances disjointly. The three states -- `current`, `clean-reunion`, `fork` --
are exactly what a reviewer needs to know before merging.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from sgt import state
from sgt.core import lens, oracle, order
from sgt.core.ideal import Ideal
from sgt.core.store import Store
from sgt.core.sync import LandReport, land as _sync_land
from sgt.lens import tree

__all__ = [
    "Proposal", "create", "status", "land", "load", "all_proposals", "render_github",
]


@dataclass(frozen=True)
class Proposal:
    """A base+Δ review object. `base_ideal_ids` is the base ref's op-set *at create time* (so
    staleness can be detected by comparing it to the base's op-set now); `delta_ids` is Δ = current −
    base; `feature_delta` is the feature ids Δ touches; `claim_key` links to the published oracle
    claim for the current (base∪Δ) ideal. `approvals` is schema + storage only in this unit -- no
    enforcement (deferred with the review surface)."""

    id: str
    base_ref: str
    base_ideal_ids: tuple[str, ...]
    delta_ids: tuple[str, ...]
    feature_delta: tuple[str, ...]
    claim_key: str | None
    title: str | None
    description: str | None
    created_ts: float
    approvals: tuple[dict, ...] = ()


def _to_body(p: Proposal) -> dict:
    return {
        "id": p.id,
        "base_ref": p.base_ref,
        "base_ideal_ids": list(p.base_ideal_ids),
        "delta_ids": list(p.delta_ids),
        "feature_delta": list(p.feature_delta),
        "claim_key": p.claim_key,
        "title": p.title,
        "description": p.description,
        "created_ts": p.created_ts,
        "approvals": list(p.approvals),
    }


def _from_body(body: dict) -> Proposal:
    return Proposal(
        id=body["id"],
        base_ref=body["base_ref"],
        base_ideal_ids=tuple(body["base_ideal_ids"]),
        delta_ids=tuple(body["delta_ids"]),
        feature_delta=tuple(body["feature_delta"]),
        claim_key=body.get("claim_key"),
        title=body.get("title"),
        description=body.get("description"),
        created_ts=body["created_ts"],
        approvals=tuple(body.get("approvals", ())),
    )


def _mint_id(base_ref: str, base_ideal_ids, delta_ids) -> str:
    """A short, stable id content-addressed by base+Δ -- two clones minting the "same" proposal
    (same base ref and Δ) produce the same file, so the G-Set dedups it on sync rather than
    double-counting (mirrors a claim's `(ideal_key, runner)` file keying)."""
    blob = "|".join([base_ref, ",".join(sorted(base_ideal_ids)), ",".join(sorted(delta_ids))])
    return sha256(blob.encode("utf-8")).hexdigest()[:12]


def _feature_delta(repo: Path, delta_ids) -> tuple[str, ...]:
    """The feature ids Δ touches, via the last `sgt map`-built tree's `op_leaf` (op-id -> feature).
    Empty when no tree has been built or Δ's ops aren't assigned to a leaf yet."""
    result = tree.load(repo)
    op_leaf = result["op_leaf"] if result else {}
    return tuple(sorted({op_leaf[op] for op in delta_ids if op in op_leaf}))


def create(repo: str | Path, base_ref: str = "main", title: str | None = None,
           description: str | None = None) -> Proposal:
    """Create a proposal = current ideal's Δ over `base_ref`'s committed ideal. `get(repo)` first
    (mine-on-contact, R9) so Δ reflects current reality. **Validity (C10):** base∪Δ must be a valid
    ideal -- downward-closed and fork-free -- else the branch forks the base (or is missing a
    prerequisite) and is not a reviewable object; `ValueError` in that case. `claim_key` links to the
    published claim for the current ideal (the contributor runs `sgt oracle publish` to attach it).
    Persisted to `.sgt/proposals/<id>.json` and returned."""
    repo = Path(repo)
    lens.get(repo)  # mine-on-contact (R9)
    all_ops = Store(repo).all_ops()
    base = lens.ideal_for_ref(repo, base_ref)
    current = lens.current_ideal(repo)
    delta = current.op_ids - base.op_ids
    union_ids = base.op_ids | delta
    if not order.is_valid_ideal(all_ops, union_ids):
        raise ValueError(
            f"base∪Δ is not a valid ideal (downward-closure or fork-freedom violated) -- the branch "
            f"forks {base_ref!r} or is missing a prerequisite; reconcile before proposing"
        )
    p = Proposal(
        id=_mint_id(base_ref, base.op_ids, delta),
        base_ref=base_ref,
        base_ideal_ids=tuple(sorted(base.op_ids)),
        delta_ids=tuple(sorted(delta)),
        feature_delta=_feature_delta(repo, delta),
        claim_key=oracle.ideal_key(current),
        title=title,
        description=description,
        created_ts=time.time(),
        approvals=(),
    )
    state.save_proposal(repo, f"{p.id}.json", _to_body(p))
    return p


def status(repo: str | Path, proposal_id: str) -> dict:
    """Staleness by re-union (C10), a pure read (persists nothing). Re-read the base's ideal *now*
    -- it may have moved since create -- and re-union it with Δ over the current op store:

    * base unchanged             -> `current`.
    * base moved, Δ still applies -> `clean-reunion` ("base advanced; Δ still applies").
    * Δ now forks the moved base  -> `fork`, with the `sgt merge-op` remedy per forked symbol.

    Also returns the feature delta and the oracle claim for base∪Δ (reduced to a valid ideal so a
    forked union still keys cleanly; empty once the base moves and re-keys the op-set)."""
    repo = Path(repo)
    p = load(repo, proposal_id)
    if p is None:
        raise ValueError(f"no proposal {proposal_id!r}")
    all_ops = Store(repo).all_ops()
    base_recorded = frozenset(p.base_ideal_ids)
    delta = frozenset(p.delta_ids)
    base_now = lens.ideal_for_ref(repo, p.base_ref)
    union_ids = base_now.op_ids | delta
    fork_triples = order.forks(all_ops, union_ids)

    if base_now.op_ids == base_recorded:
        state_name, note = "current", None
    elif fork_triples:
        state_name = "fork"
        note = "base reworked a symbol Δ also touches; reconcile with sgt merge-op"
    else:
        state_name, note = "clean-reunion", "base advanced; Δ still applies"

    claim_ideal = Ideal.from_ops(order.reduce_to_ideal(union_ids, all_ops), all_ops)
    forks_out = [
        {"symbol": sym, "tips": [a, b], "remedy": f"sgt merge-op {a[:8]} {b[:8]}"}
        for sym, a, b in fork_triples
    ]
    return {
        "state": state_name,
        "note": note,
        "base_ref": p.base_ref,
        "base_moved": base_now.op_ids != base_recorded,
        "feature_delta": list(p.feature_delta),
        "delta_op_count": len(delta),
        "forks": forks_out,
        "remedy": forks_out[0]["remedy"] if forks_out else None,
        "claim": oracle.claim_for(repo, claim_ideal),
    }


def land(repo: str | Path, proposal_id: str, accept_ids=None) -> LandReport:
    """Land a proposal onto its base branch. Refuse a stale-forked proposal (a `fork` status) with a
    blocked report rather than advancing over the fork. Otherwise delegate to the U23 CAS advance
    (`sync.land`) on the base ref's branch -- the session's HEAD carries Δ, which `land` unions onto
    the (possibly moved) branch tip and gates oracle-green.

    Partial acceptance: `accept_ids`, a subset of Δ that must itself be downward-closed on top of the
    base (`is_valid_ideal(base ∪ accept_ids)`), narrows what lands; the default (`None`) accepts all
    of Δ. A proper subset is materialized onto HEAD first (`lens.put`), so `sync.land`'s re-union with
    the live branch tip lands exactly base_now∪accept."""
    repo = Path(repo)
    p = load(repo, proposal_id)
    if p is None:
        raise ValueError(f"no proposal {proposal_id!r}")
    branch = p.base_ref.rsplit("/", 1)[-1]  # strip a refs/heads/ prefix if present

    st = status(repo, proposal_id)
    if st["state"] == "fork":
        return LandReport(
            branch=branch, landed=False,
            blocked_reason=f"proposal is stale -- base forked underneath Δ; {st['remedy']}",
            forks=tuple((f["symbol"], f["tips"][0], f["tips"][1]) for f in st["forks"]),
        )

    if accept_ids is not None:
        accept = frozenset(accept_ids)
        delta = frozenset(p.delta_ids)
        if not accept <= delta:
            raise ValueError("accept_ids must be a subset of the proposal's Δ")
        all_ops = Store(repo).all_ops()
        base_ids = frozenset(p.base_ideal_ids)
        if not order.is_valid_ideal(all_ops, base_ids | accept):
            raise ValueError("base∪accept_ids is not downward-closed -- widen the accepted subset")
        if accept != delta:
            accepted = Ideal.from_ops(base_ids | accept, all_ops)
            put_sha = lens.put(repo, accepted, message=f"sgt propose: accept subset of {p.id}")
            lens.record_ideal(repo, accepted, put_sha)

    return _sync_land(repo, branch=branch)


def load(repo: str | Path, proposal_id: str) -> Proposal | None:
    """The proposal with this id from the working tree, or `None` if absent (a pure read)."""
    body = state.load_proposal(Path(repo), f"{proposal_id}.json")
    return _from_body(body) if body is not None else None


def all_proposals(repo: str | Path) -> list[Proposal]:
    """Every proposal in the working tree, sorted by id (a pure read)."""
    repo = Path(repo)
    out = [
        _from_body(body)
        for name in state.list_proposal_files(repo)
        if (body := state.load_proposal(repo, name)) is not None
    ]
    return sorted(out, key=lambda p: p.id)


# -- GitHub rendering: a pure projection of the view (no network, no `gh`) ------------------------

def render_github(view: dict) -> dict:
    """Render a `sgt.api.proposal_view` as a GitHub PR: `{"branch", "pr_title", "pr_body"}`. The
    `pr_body` is plain markdown a reviewer *without sgt* can act on -- a feature-delta table, the
    oracle claim (status + runner identity), a provenance summary, and any staleness/fork warning up
    top. A pure function of the view (no GitHub API, no `gh`): the template seam a follow-on
    GitLab/other-forge renderer slots into (plan Open Questions)."""
    pid = view["id"]
    title = view.get("title") or f"sgt proposal {pid}"
    lines: list[str] = []
    if view.get("description"):
        lines += [view["description"], ""]
    lines += [
        f"**Base:** `{view['base_ref']}` — **Δ:** {view['delta_op_count']} op(s) across "
        f"{len(view['feature_delta'])} feature(s)",
        "",
    ]
    # The staleness/fork banner comes first -- it is the thing a reviewer must see before merging.
    lines += _render_status_banner(view["status"])

    lines += ["### Feature delta", "", "| Feature | Label | Ops |", "| --- | --- | --- |"]
    for f in view["feature_delta"]:
        lines.append(f"| `{f['feature_id']}` | {f['label']} | {f['op_count']} |")
    if not view["feature_delta"]:
        lines.append("| _(none)_ | | |")
    lines += ["", *_render_claim(view["claim"]), *_render_provenance(view["provenance"])]

    return {
        "branch": _branch_name(pid, view.get("title")),
        "pr_title": title,
        "pr_body": "\n".join(lines).rstrip() + "\n",
    }


def _branch_name(pid: str, title: str | None) -> str:
    """A suggested branch name: a slug of the title plus a short id, or `sgt/proposal-<id>`."""
    if title:
        slug = "-".join(filter(None, "".join(c if c.isalnum() else "-" for c in title.lower()).split("-")))
        if slug:
            return f"sgt/{slug[:40].rstrip('-')}-{pid[:8]}"
    return f"sgt/proposal-{pid[:8]}"


def _render_status_banner(st: dict) -> list[str]:
    if st["state"] == "fork":
        out = ["> **⚠ Stale:** the base reworked a symbol this proposal also changes — "
               "resolve the fork before merging:", ""]
        out += [f"- `{f['remedy']}` (symbol `{f['symbol']}`)" for f in st["forks"]]
        return out + [""]
    if st["state"] == "clean-reunion":
        return [f"> **Note:** {st['note']}.", ""]
    return []


def _render_claim(claim) -> list[str]:
    out = ["### Oracle claim", ""]
    if not claim:
        return out + ["_No published claim for this op-set._", ""]
    for c in claim:
        runner = c.get("runner", {})
        out.append(
            f"- **{c.get('status', '?')}** — ran by `{runner.get('by') or 'unknown'}` on "
            f"`{runner.get('host') or 'unknown-host'}` (Python {runner.get('python') or '?'}), "
            f"key `{c.get('ideal_key', '?')}`"
        )
    return out + [""]


def _render_provenance(provenance) -> list[str]:
    out = ["### Provenance", ""]
    if not provenance:
        return out + ["_No recorded provenance._", ""]
    sessions = sorted({e["session"] for e in provenance if e.get("session")})
    summary = f"{len(provenance)} witnessing commit(s)"
    if sessions:
        summary += f"; sessions: {', '.join(sessions)}"
    return out + [summary, ""]
