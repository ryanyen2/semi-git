"""Selection resolution: what a user-supplied token *denotes*.

A "selection" is the argument of the daily spine (`sgt revert <sel>`, `sgt restore <sel>`,
`sgt show <sel>`) -- a checkpoint, a feature, an op, or a symbol. This package owns the
deterministic identification ladder shared by every verb that takes one; each verb keeps its own
mutation planning.
"""

from sgt.select.resolve import Selection, identify, is_checkpoint_shaped, is_handle_shaped

__all__ = ["Selection", "identify", "is_checkpoint_shaped", "is_handle_shaped"]
