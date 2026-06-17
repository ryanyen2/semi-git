"""Feature lifecycle algebra: revert (by closure + GC), switch (suspend/restore)."""

from sgt.lifecycle.algebra import RevertOutcome, revert_feature, switch_feature

__all__ = ["RevertOutcome", "revert_feature", "switch_feature"]
