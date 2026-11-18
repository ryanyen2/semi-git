"""Live prompt capture (plan U3, KTD5): a committed sidecar recording the prompt/intent text an
agent or user was given, keyed on the same provenance keys `Attribution` already carries -- a
plan-intake session id, a `ugh session` name, or a commit sha. `Op`/`Attribution` stay untouched
(KTD1): prompts attach to a *plan/session*, not per-op, and free text has no business inside the
frozen, content-addressed, merge-semilattice `Op`.

Write-once per key, which is what makes the sync merge trivial (`merge`, U5): a G-Set union by key
is conflict-free only because no key's value ever changes after it is first observed.
"""

from __future__ import annotations

from pathlib import Path

from ugh import state

_ARTIFACT = "intent_prompts"


def load_prompts(repo: str | Path) -> dict[str, str]:
    """The whole working-tree prompt sidecar -- `sync`'s `ingest` reads this for "ours" side."""
    return state.load_json(repo, _ARTIFACT, default={})


def prompts_at(gb, sha: str) -> dict[str, str]:
    """A teammate's prompt sidecar as committed at `sha` -- the historical-blob read `sync`
    unions."""
    body = state.load_blob_json(gb, sha, _ARTIFACT)
    return {} if body is None else dict(body)


def prompt_for(repo: str | Path, key: str) -> str | None:
    """The recorded prompt for `key`, or `None` if absent -- never raises on a missing key."""
    return load_prompts(repo).get(key)


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
