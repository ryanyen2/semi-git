"""Live prompt capture (plan U3, KTD5): a committed sidecar recording the prompt/intent text an
agent or user was given, keyed on the same provenance keys `Attribution` already carries -- a
plan-intake session id, a `sgt session` name, or a commit sha. `Op`/`Attribution` stay untouched
(KTD1): prompts attach to a *plan/session*, not per-op, and free text has no business inside the
frozen, content-addressed, merge-semilattice `Op`.

Write-once per key: the first `record_prompt` for a key wins, and a later call on an existing key
is a no-op. This is what makes the sync merge trivial (`merge`, U5) -- a G-Set union by key is
conflict-free only because no key's value ever changes after it is first observed, mirroring
`sgt.lens.reconcile.union_aliases`'s G-Set idiom.
"""

from __future__ import annotations

from pathlib import Path

from sgt import state

_ARTIFACT = "intent_prompts"


def _load(repo: str | Path) -> dict[str, str]:
    return state.load_json(repo, _ARTIFACT, default={})


def record_prompt(repo: str | Path, key: str, text: str) -> bool:
    """Record `text` under `key` (a plan-id, session-name, or commit sha) if `key` has no prompt
    yet. Returns whether anything was written -- `False` for an empty `key`/`text` or an existing
    key (write-once; a second write is a deliberate no-op, never an overwrite)."""
    if not key or not text:
        return False
    prompts = _load(repo)
    if key in prompts:
        return False
    prompts[key] = text
    state.save_json_if_changed(repo, _ARTIFACT, prompts)
    return True


def prompt_for(repo: str | Path, key: str) -> str | None:
    """The recorded prompt for `key`, or `None` if absent -- never raises on a missing key."""
    return _load(repo).get(key)


def merge(ours: dict[str, str], theirs: dict[str, str]) -> dict[str, str]:
    """Union two clones' prompt sidecars by key (U5): since a key's value is write-once, the only
    possible collision is both sides recording the *same* key, in which case either value is an
    equally-valid "first write" and the merge picks deterministically (the smaller string, so the
    result never depends on which side is `ours`) rather than crashing or silently preferring one
    side. Commutative, associative, idempotent -- a plain G-Set-by-key join."""
    merged = dict(ours)
    for key, text in theirs.items():
        if key not in merged:
            merged[key] = text
        elif merged[key] != text:
            merged[key] = min(merged[key], text)
    return merged
