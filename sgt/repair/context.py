"""Compressed per-hollow context for one repair attempt (plan U2; Agentless "localize tightly").

Deliberately excludes whole files, the repo map, and the transitive call graph: a backend gets
exactly what a human fulfilling the hollow by hand would want -- the symbol's current bytes, what
it must stop depending on and why, one hop of what it may still legitimately depend on, and (on a
retry) the specific reason the last attempt was rejected.
"""

from __future__ import annotations

from pathlib import Path

from sgt.core import rewrite
from sgt.core.op import Op
from sgt.core.store import Store
from sgt.repair.backends import RepairRequest


def _first_line(image: bytes | None) -> str:
    if not image:
        return ""
    return image.decode("utf-8", errors="replace").splitlines()[0].strip()


def build_request(
    repo: str | Path, draft: rewrite.RewriteDraft, hollow_id: str, *,
    attempt: int = 1, feedback: str | None = None,
) -> RepairRequest:
    """Context for repairing `hollow_id` (one of `draft.hollow_ids`, drafted by
    `revert_keep_dependents`) so its symbol no longer depends on the reverted target."""
    repo = Path(repo)
    store = Store(repo)
    ops = store.all_ops()
    by_id = {op.id: op for op in ops}

    hollow = store.get_hollow(hollow_id)
    if hollow is None:
        raise ValueError(f"hollow {hollow_id[:12]} not found -- already fulfilled or never drafted")
    sym = next(iter(hollow.footprint))
    before, _pending = hollow.footprint[sym]

    target_op = by_id.get(draft.target)
    if target_op is None:
        raise ValueError(f"draft target {draft.target[:12]} not found in the store")
    removed_symbol = next(iter(target_op.footprint))
    removed_image = target_op.images.get(removed_symbol)

    # The op that produced `sym`'s pre-removal content -- the dependent `revert_keep_dependents`
    # drafted this hollow for. Its own `requires` is exactly sym's one-hop dependency set.
    producer_op = (
        next((op for op in ops if sym in op.footprint and op.footprint[sym][1] == before), None)
        if before is not None else None
    )

    try:
        current_image = rewrite._entity_bytes_from_tree(repo, sym)
    except rewrite.RewriteError:
        current_image = (producer_op.images.get(sym) if producer_op else None) or b""

    neighbors: list[str] = []
    if producer_op is not None:
        version_index: dict[tuple[str, str], Op] = {
            (s, after): op for op in ops for s, (_, after) in op.footprint.items()
        }
        for req_sym, req_ver in sorted(producer_op.requires):
            if req_sym == removed_symbol:
                continue
            req_op = version_index.get((req_sym, req_ver))
            line = _first_line(req_op.images.get(req_sym)) if req_op else ""
            if line:
                neighbors.append(line)

    return RepairRequest(
        symbol=sym,
        current_image=current_image.decode("utf-8", errors="replace"),
        removed_symbol=removed_symbol,
        removed_intent=target_op.intent,
        removed_signature=_first_line(removed_image),
        neighbors=neighbors,
        attempt=attempt,
        feedback=feedback,
    )
