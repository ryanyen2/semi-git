"""`sgt find`: the corpus, the two rungs, and the promise not to touch anything.

The semantic rung's ranking is tested against injected vectors rather than a
live endpoint. Embedding calls cost money and need a key, so a test that made
them would be a test that silently stops running -- which is exactly how the
`difflib` rung this replaces went years without anyone noticing it could not
answer its own documented example.
"""

from __future__ import annotations

import json

import pytest

from sgt.core.lens import init as kernel_init
from sgt.lens import search


@pytest.fixture()
def repo(tmp_path):
    """A repo with two clearly different concerns in it."""
    import subprocess

    root = tmp_path / "r"
    (root / "app").mkdir(parents=True)
    (root / "app" / "slots.py").write_text(
        "def parse_slot(text):\n    return text.strip().lower()\n"
    )
    (root / "app" / "search.py").write_text(
        "def find_courses(term):\n    return [c for c in term]\n"
    )
    for cmd in (["init", "-q"], ["config", "user.email", "t@e.org"], ["config", "user.name", "T"]):
        subprocess.run(["git", *cmd], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "add course search and slot parsing"],
                   cwd=root, check=True, capture_output=True)
    kernel_init(str(root))
    return root


def test_corpus_covers_saves(repo):
    entries = search.corpus(repo)
    assert any(e["kind"] == "save" for e in entries), \
        "a commit message is the most searchable thing there is"


def test_corpus_covers_features_and_their_symbols(repo, monkeypatch):
    """A feature carries its label, the sentence about it, and its symbols; each
    symbol is findable on its own and knows which feature to take you to."""
    monkeypatch.setattr(
        "sgt.api.map_view",
        lambda *_a, **_k: {"nodes": [
            {
                "id": "f-abc", "kind": "feature", "label": "Time Slots",
                "why": "parses, formats and validates scheduled time slots",
                "members": ["app/slots.py::parse_slot", "app/slots.py::__residue__::x"],
            },
            # A subsystem's `why` mentions everything under it, so indexing it
            # made the whole-repo root the top hit for any query. Must be skipped.
            {
                "id": "N0", "kind": "subsystem", "label": "Everything",
                "why": "spans time slots and all else",
                "members": ["app/slots.py::parse_slot"],
            },
        ]},
    )
    entries = search.corpus(repo)
    features = [e for e in entries if e["kind"] == "feature"]
    assert [f["id"] for f in features] == ["f-abc"], "subsystem nodes must not be indexed"
    feature = features[0]
    assert "formats" in feature["text"], "the labeller's sentence is the searchable part"
    assert "parse slot" in feature["text"], "so are the identifiers under it"

    symbols = [e for e in entries if e["kind"] == "symbol"]
    assert [s["id"] for s in symbols] == ["app/slots.py::parse_slot"], \
        "bookkeeping sentinels are not things anyone searches for"
    assert symbols[0]["feature"] == "f-abc"


def test_symbol_words_makes_identifiers_searchable():
    # The lexical rung has nothing else to work with, and a query is written in
    # words, not in `file.py::snake_case`.
    assert search._symbol_words("coursecraft/slots.py::parse_slot") == "coursecraft slots parse slot"
    assert "residue" not in search._symbol_words("tests/t.py::__residue__::HEAD")


def test_lexical_rung_answers_when_there_is_no_key(repo, monkeypatch):
    monkeypatch.setattr(search, "_embed", lambda *a, **k: None)
    view = search.search(repo, "course search", k=5)
    assert view["mode"] == "lexical"
    assert view["hits"], "word overlap should still find a commit that says those words"


def test_a_query_nobody_can_lexically_match_says_so(repo, monkeypatch):
    monkeypatch.setattr(search, "_embed", lambda *a, **k: None)
    view = search.search(repo, "zzzq wibble", k=5)
    assert view["hits"] == []
    assert view["ok"] is False


def test_semantic_rung_ranks_by_cosine(repo, monkeypatch):
    """With vectors present, the nearest entry wins -- and the mode says so."""
    index = {"model": "test", "dims": 2, "entries": [
        {"kind": "feature", "id": "f-near", "label": "near", "detail": "", "text": "near",
         "feature": "f-near", "vec": [1.0, 0.05]},
        {"kind": "feature", "id": "f-far", "label": "far", "detail": "", "text": "far",
         "feature": "f-far", "vec": [0.0, 1.0]},
    ]}
    path = repo / search.INDEX_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index))

    monkeypatch.setattr(search, "_embed", lambda *a, **k: [[1.0, 0.0]])
    view = search.search(repo, "anything", k=3)
    assert view["mode"] == "semantic"
    assert [h["id"] for h in view["hits"]] == ["f-near", "f-far"]
    assert view["hits"][0]["score"] > view["hits"][1]["score"]


def test_search_changes_nothing(repo, monkeypatch):
    """Report-only, and it has to stay that way: this is the verb people reach
    for when they are lost, and it must never be the one that cost them work."""
    import subprocess

    monkeypatch.setattr(search, "_embed", lambda *a, **k: None)
    before = subprocess.run(["git", "status", "--porcelain=v1", "-uall"],
                            cwd=repo, capture_output=True, text=True).stdout
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout

    search.search(repo, "parse", k=5)

    after = subprocess.run(["git", "status", "--porcelain=v1", "-uall"],
                           cwd=repo, capture_output=True, text=True).stdout
    head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                capture_output=True, text=True).stdout
    # `.sgt/` is the tool's own scratch space; everything else must be untouched.
    strip = lambda s: "\n".join(x for x in s.splitlines() if ".sgt/" not in x)
    assert strip(before) == strip(after)
    assert head == head_after
