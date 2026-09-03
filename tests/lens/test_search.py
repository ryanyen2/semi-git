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


def test_cross_feature_work_is_indexed_under_the_label_a_verb_accepts(repo, monkeypatch):
    """The ◆ row: the one unit `sgt revert`/`sgt restore` take by name, and the one thing find
    could not return. Indexed under the label, not the theme id, because the hit's own next-step
    is `sgt show "<label>"` and that has to be a command that runs."""
    monkeypatch.setattr(
        "sgt.api.intent_view",
        lambda *_a, **_k: {"themes": [
            {
                "label": "Event Day Handling", "rationale": "Tracks exceptional days and keeps "
                                                            "them out of the averages.",
                "feature_span": ["f-a", "f-b", "f-c"], "atom_shas": ["sha1", "sha2"],
            },
            # One lane: that feature is already indexed and answers as itself.
            {"label": "One Lane", "rationale": "x", "feature_span": ["f-a"],
             "atom_shas": ["sha1", "sha2"]},
            # One save: it IS that save. Its label is the commit subject and its rationale is the
            # "Ungrouped commit." placeholder, so indexing it listed the same words twice with
            # bookkeeping prose under the second copy.
            {"label": "add a csv download link", "rationale": "Ungrouped commit.",
             "feature_span": ["f-a", "f-b"], "atom_shas": ["sha9"]},
        ]},
    )
    work = [e for e in search.corpus(repo) if e["kind"] == "work"]
    assert [w["label"] for w in work] == ["Event Day Handling"]
    assert work[0]["id"] == "Event Day Handling", "the label is the handle"
    assert "across 3 features" in work[0]["detail"]
    assert "exceptional" in work[0]["text"], "the rationale is the searchable part"


def test_a_save_says_which_piece_of_work_it_belongs_to(repo, monkeypatch):
    """A save hit used to print its own sha twice (`<sha>  save <sha>`) -- the one kind of hit
    with no context, where a feature gives its description and a symbol names its lane."""
    monkeypatch.setattr(
        "sgt.api.intent_view",
        lambda *_a, **_k: {"themes": [{
            "label": "Event Day Handling", "rationale": "why",
            "feature_span": ["f-a", "f-b"], "atom_shas": ["sha1", "sha2"],
        }]},
    )
    monkeypatch.setattr(
        "sgt.api.history_view",
        lambda *_a, **_k: {"commits": [
            {"sha": "sha1" + "0" * 36, "subject": "keep event days out of the averages"},
            {"sha": "sha1", "subject": "mark event days on the charts"},
            {"sha": "beef" + "0" * 36, "subject": "an unrelated save"},
        ]},
    )
    saves = {e["label"]: e for e in search.corpus(repo) if e["kind"] == "save"}
    assert saves["mark event days on the charts"]["detail"] == "part of ◆ Event Day Handling"
    assert saves["an unrelated save"]["detail"] == "", "no ◆ claims it; say nothing rather than 'save <sha>'"
    assert saves["an unrelated save"]["id"] == "beef000", "seven, like every other surface prints a sha"


def test_one_feature_cannot_fill_the_result_list_with_its_own_symbols(repo, monkeypatch):
    """Measured on the study's bikecount bundle: "the bit that works out the averages" filled four
    of five slots with `hourly_averages`, `::_weekday`, `::_weekend` and `::monthly_totals` -- four
    ways of saying one lane -- and pushed the work that changed how an average is computed off the
    list. Whoever is reading has to open each hit to learn they are the same answer."""
    entries = [
        {"kind": "symbol", "id": f"a.py::f{i}", "label": f"a.py::f{i}", "detail": "", "text": "x",
         "feature": "f-one", "vec": [1.0, 0.0]}
        for i in range(4)
    ] + [
        {"kind": "save", "id": "s1", "label": "the save that answers", "detail": "", "text": "x",
         "feature": "", "vec": [0.9, 0.0]},
    ]
    path = repo / search.INDEX_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": "test", "dims": 2, "entries": entries}))
    monkeypatch.setattr(search, "_embed", lambda *a, **k: [[1.0, 0.0]])

    hits = search.search(repo, "anything", k=3)["hits"]
    assert [h["kind"] for h in hits] == ["symbol", "symbol", "save"], \
        "two symbols per feature, then the next different thing"
    assert hits[2]["label"] == "the save that answers"
