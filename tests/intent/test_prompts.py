"""Tests for ugh.intent.prompts -- the committed prompt sidecar: a read by key and a G-Set-by-key
merge."""

from __future__ import annotations

from ugh.intent import prompts
from ugh.store.gitbind import init_store


def test_missing_key_returns_none(tmp_path):
    init_store(tmp_path)
    assert prompts.prompt_for(tmp_path, "no-such-key") is None


def test_merge_unions_distinct_keys(tmp_path):
    ours = {"a": "prompt a"}
    theirs = {"b": "prompt b"}
    merged = prompts.merge(ours, theirs)
    assert merged == {"a": "prompt a", "b": "prompt b"}


def test_merge_same_key_both_sides_is_deterministic_no_crash(tmp_path):
    ours = {"a": "prompt one"}
    theirs = {"a": "prompt two"}
    merged_1 = prompts.merge(ours, theirs)
    merged_2 = prompts.merge(theirs, ours)
    assert merged_1 == merged_2  # symmetric, no duplicate/crash on a genuine same-key collision


