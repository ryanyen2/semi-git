"""Shared LLM-confinement guard (plan U7/KTD6, R9): the one enforced rule that an LLM's output
must never smuggle in an id it was never shown. Both `sgt.intent.theme` (scope-less coalescing)
and `sgt.intent.resolve` (NL target resolution) name candidates the LLM invented from a fixed
vocabulary -- this used to be enforced independently in each module; now both call the same
pure function so the invariant can't silently diverge between them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable


def filter_to_shown(items: list, shown_keys: frozenset[str], keys_of: Callable[[object], Iterable[str]]) -> list:
    """Keep only the items every one of whose `keys_of(item)` keys is in `shown_keys`. An item
    naming even one id it was never shown is dropped whole, never trusted -- this is a pure
    set-intersection check, no exceptions, no partial trust."""
    shown = frozenset(shown_keys)
    return [item for item in items if frozenset(keys_of(item)) <= shown]
