"""Tests for ugh.intent.align -- the prompt tokenizer `revert`'s ledger rung borrows."""

from __future__ import annotations

from ugh.intent.align import _content_words


def test_content_words_keeps_topical_tokens_drops_fillers_and_shorts():
    w = _content_words("make the search better, fix the bug")
    assert "search" in w          # a real topic word survives
    assert "make" not in w        # dev-filler stopword dropped
    assert "better" not in w      # dev-filler stopword dropped
    assert "bug" not in w         # too short (< 4) -- contentless, correctly no anchor
    assert "the" not in w         # too short
