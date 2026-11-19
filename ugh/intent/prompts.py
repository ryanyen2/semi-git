"""Captured prompts: the text an agent or person was given, keyed by a commit sha or a capture
key. Write-once per key, so two clones' tables merge by a plain union."""

from __future__ import annotations

from pathlib import Path

from ugh import state

_ARTIFACT = "intent_prompts"


def load_prompts(repo: str | Path) -> dict[str, str]:
    """The working tree's prompt table, key -> text."""
    return state.load_json(repo, _ARTIFACT, default={})


def prompts_at(gb, sha: str) -> dict[str, str]:
    """The prompt table as committed at `sha` (a state-ref commit)."""
    body = state.load_blob_json(gb, sha, _ARTIFACT)
    return {} if body is None else dict(body)


def prompt_for(repo: str | Path, key: str) -> str | None:
    """The recorded prompt for `key`, or `None`."""
    return load_prompts(repo).get(key)


def merge(ours: dict[str, str], theirs: dict[str, str]) -> dict[str, str]:
    """Union two prompt tables by key. A key both sides recorded differently keeps the smaller
    text, so the result does not depend on which side is `ours`."""
    merged = dict(ours)
    for key, text in theirs.items():
        if key not in merged:
            merged[key] = text
        elif merged[key] != text:
            merged[key] = min(merged[key], text)
    return merged
