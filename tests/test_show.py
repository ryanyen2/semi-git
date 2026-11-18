"""`ugh show <address>`: the steps at an address, the ask behind each save it touches, and the
planner's answer. It is what a cautious user runs before a mutating verb, so it must write nothing,
and a miss must say so with an honest exit code."""

from __future__ import annotations

import json

from ugh import api
from ugh.cli import main
from ugh.core.lens import get
from ugh.store.gitbind import GitBinding
from tests.laws import corpus


def _repo(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    return repo


# Pure derived caches: a read is allowed to refresh them, because every read mines on contact and
# the whole point of these files is to make the next read cheap.
_DERIVED_CACHES = frozenset({
    "local/ops_dirstat.json", "local/derive_cache.json", "local/extract_cache.json",
    "local/refs_cache.json", "local/sync_cache.json", "local/op_index.json",
})


def test_show_writes_nothing(tmp_path):
    """Pinned by hashing every file under `.sgt/` before and after: the answer comes from the
    planner, and a plan that leaked a write would make the safe pre-flight check destructive."""
    repo = _repo(tmp_path)
    sgt_dir = repo / ".sgt"

    def snapshot():
        return {rel: p.read_bytes()
                for p in sorted(sgt_dir.rglob("*")) if p.is_file()
                for rel in [p.relative_to(sgt_dir).as_posix()]
                if rel not in _DERIVED_CACHES}

    before = snapshot()
    api.show_view(repo, GitBinding(repo).head()[:7])
    assert snapshot() == before


def test_cli_show_json_and_text_agree_and_exit_codes_are_honest(tmp_path, capsys, monkeypatch):
    repo = _repo(tmp_path)
    sha = GitBinding(repo).head()[:7]
    monkeypatch.chdir(repo)

    assert main(["show", sha]) == 0
    text = capsys.readouterr().out
    assert text.startswith(f"{sha}: ")

    assert main(["show", "--json", sha]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and all(st["address"].endswith(f"@{sha}") for st in payload["steps"])
    assert f"{sha} " in text and payload["steps"][0]["address"] in text

    assert main(["show", "definitely-not-a-thing"]) == 2
    assert "no such address" in capsys.readouterr().out


def _asked_repo(tmp_path):
    """A one-save repo whose save closed a capture window with a real, messy prompt in it."""
    from ugh.core.store import Store
    from ugh.intent.activity import record_activity
    from ugh.intent.manifest import record_manifest
    from ugh.intent.turns import record_turn
    from ugh.store.gitbind import init_store

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "pages.py").write_text("def render():\n    return 1\n", encoding="utf-8")
    sha = gb.commit_all("add the daily page")
    get(repo)
    prompt = ("so i think we shoudl proably add teh daily page for teh committee, becuase they "
              "keep emailing me spreadsheets and i do it by hand every single time")
    record_turn(repo, key="cs-1", key_kind="chat", actor="human", channel="hook", text=prompt,
                ts=10.0)
    record_activity(repo, tool="Edit", file="pages.py", session_id="cs-1", ts=20.0)
    ops = [op for op in Store(repo).all_ops()]
    record_manifest(repo, sha=sha, ops=ops, end=30.0, prev_save_ts=0.0)
    return repo, sha, prompt


def test_show_says_what_a_save_was_asked_for(tmp_path):
    repo, sha, prompt = _asked_repo(tmp_path)
    ask = api.show_view(repo, sha[:7])["saves"][0]["ask"]
    assert ask["gist"] == "add teh daily page for teh committee"
    assert ask["text"] == prompt and ask["source"] == "you, in a Claude Code chat"


def test_show_says_nothing_about_asks_for_history_that_predates_capture(tmp_path):
    repo = _repo(tmp_path)
    view = api.show_view(repo, GitBinding(repo).head()[:7])
    assert view["saves"][0]["ask"] is None


def test_the_asked_line_is_an_excerpt_when_rendered(tmp_path, capsys, monkeypatch):
    repo, sha, prompt = _asked_repo(tmp_path)
    monkeypatch.chdir(repo)
    assert main(["show", sha[:7]]) == 0
    out = capsys.readouterr().out
    assert "“add teh daily page for teh committee”" in out
    assert prompt not in out
