"""The deterministic selection ladder (Phase 3 item 5).

`sgt revert`/`sgt restore` grew their own copy of this ladder inline in `cli/ideal_edit.py`
(`_kernel_edit_verb`), where "does this token resolve?" was answered by "can I plan a revert of
it?". That entanglement makes the ladder unusable by any verb that only wants to *identify* a token
-- `sgt show` being the obvious one -- and it means two callers can disagree about what the same
token names, which is the worst outcome for a user who copied an id out of one view and typed it
into another verb.

So the seam here is identification vs. planning:

* **identification** (this module) is verb-independent, deterministic, and side-effect free: given a
  token, say which *kind* of thing it names and which ops it covers. No LLM, no mining, no mutation.
* **planning** stays with each verb, because it genuinely differs -- `revert` needs the target live
  in the ideal, `restore` needs it absent, and the two produce different previews.

Rung order is deliberately identical to `_kernel_edit_verb`'s, because `sgt show <x>` promising one
reading while `sgt revert <x>` acts on another is precisely the confusion the extraction exists to
prevent. The NL rungs (ledger phrase match, then the LLM resolver) are *not* here: they are
mutation-flavored, and an inspect verb must stay instant and offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# A feature handle as the graph/log print it: `f-`-prefixed or the bare hex copy token. A full op id
# is all-hex too, which is why `identify` tries the feature rung first for this shape and falls back
# to the op rung -- the feature scope wins over the single op it shadows (the log prints the founding
# op's hex *as* the feature's handle).
_HANDLE = re.compile(r"(f-)?[0-9a-f]{3,}\Z")


@dataclass(frozen=True)
class Selection:
    """What a token denotes. `op_ids` is always the full covered set -- one element for an `op`/
    `symbol` selection, the whole segment/feature op-set for a `checkpoint`/`feature` -- so callers
    can treat every kind uniformly and only branch on `kind` for wording."""

    kind: str  # "checkpoint" | "feature" | "op" | "symbol"
    target: str  # the token exactly as the user typed it
    op_ids: frozenset[str]
    feature_id: str | None = None
    label: str | None = None

    @property
    def is_group(self) -> bool:
        """True when the token names a *set* the user thinks of as one thing (a feature or a
        checkpoint) rather than a single edit -- the distinction that decides whether a consequence
        preview should be phrased per-op or per-feature."""
        return self.kind in ("checkpoint", "feature")


def is_checkpoint_shaped(target: str) -> bool:
    """`<feature>@<n>` or `<feature>:<slug>` -- the intent-segment rewind unit. Neither `@<digits>`
    nor a lone `:` appears in a feature handle or an op id, so the shape is unambiguous and is tried
    first. `::` is excluded: that is a `file::name` symbol, and letting it in here would send every
    symbol through a pointless checkpoint lookup. (The cost of the exclusion is that a feature
    *label* containing `::` can't be used as a checkpoint's feature part -- labels are prose, so this
    has never come up, and `@<n>` remains available for it.)"""
    if "::" in target:
        return False
    return ("@" in target and target.rpartition("@")[2].isdigit()) or ":" in target


def is_handle_shaped(target: str) -> bool:
    """A bare-hex / `f-` handle. Symbols carry `::` and checkpoints carry `@`/`:`, so neither
    reaches this test."""
    return _HANDLE.fullmatch(target) is not None


def identify(repo: str | Path, target: str) -> Selection | None:
    """Resolve `target` to a `Selection`, or `None` when no deterministic rung claims it.

    Ladder (same precedence as `revert`/`restore`):

    1. checkpoint spec (`f-ab12@3`, `f-ab12:add-retry`)
    2. handle-shaped token -> feature first, then the op whose hex it shadows
    3. otherwise -> op / symbol first, then an exact feature *label*

    `None` means "no id, symbol, feature, or checkpoint by this name" -- the caller decides whether
    to fall through to an NL rung (`revert`) or to explain and stop (`show`).
    """
    target = target.strip()
    if not target:
        return None

    if is_checkpoint_shaped(target):
        found = _checkpoint(repo, target)
        if found is not None:
            return found
        # Not a resolving checkpoint: fall through rather than fail, so a label that merely
        # contains a colon still gets its feature/op rungs.

    if is_handle_shaped(target):
        return _feature(repo, target) or _op(repo, target)
    return _op(repo, target) or _feature(repo, target)


def _checkpoint(repo: str | Path, target: str) -> Selection | None:
    from sgt.intent.segment import resolve_checkpoint

    resolved = resolve_checkpoint(repo, target)
    if resolved is None:
        return None
    op_ids, label = resolved
    return Selection(kind="checkpoint", target=target, op_ids=frozenset(op_ids),
                     feature_id=_owning_feature(repo, op_ids), label=label)


def _feature(repo: str | Path, target: str) -> Selection | None:
    from sgt.lens.verbs import resolve_feature

    resolved = resolve_feature(repo, target)
    if resolved is None:
        return None
    op_ids, feature_id, label = resolved
    return Selection(kind="feature", target=target, op_ids=frozenset(op_ids),
                     feature_id=feature_id, label=label)


def _op(repo: str | Path, target: str) -> Selection | None:
    """An exact op id, a unique op-id prefix, or a `file::name` symbol (its frontier tip). Uses the
    kernel's own `resolve_target`, so an id `show` accepts is exactly an id `revert` accepts."""
    from sgt.core import lens, opindex, order
    from sgt.core.verbs import resolve_target

    repo = Path(repo)
    ops = opindex.index_ops(repo)
    ideal = lens.current_ideal(repo)
    op_id, err = resolve_target(ideal, ops, target)
    if err or op_id is None:
        return None
    # Frontier membership -- not the presence of `::` -- is what makes a token a symbol: a non-code
    # file is a whole-file symbol keyed by its bare path, and calling that an "op" in a `show` line
    # would name the wrong thing back at the user.
    is_symbol = target in order.frontier(ideal.op_ids, ops)
    return Selection(kind="symbol" if is_symbol else "op", target=target,
                     op_ids=frozenset({op_id}), feature_id=_owning_feature(repo, [op_id]))


def _owning_feature(repo: str | Path, op_ids) -> str | None:
    """The leaf feature that owns the most of `op_ids` -- the feature a user would name when asked
    "where does this live?". Ops in one checkpoint always share a feature, so for those the tally is
    unanimous; for a single op it is just that op's leaf."""
    from collections import Counter

    from sgt.lens.tree import load as load_tree

    op_leaf = (load_tree(repo) or {}).get("op_leaf", {})
    tally = Counter(op_leaf[o] for o in op_ids if o in op_leaf)
    return tally.most_common(1)[0][0] if tally else None
