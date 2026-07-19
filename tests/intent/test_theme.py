"""Tests for sgt.intent.theme -- the intent overlay's rung 2 (U4/KTD4/KTD7): LLM naming +
scope-less coalescing, cached by content-hash, with a deterministic offline fallback. `FakeClient`/
`_FakeResponses` mirror `tests/intent/test_resolve.py`'s idiom -- no network or API key needed."""

from __future__ import annotations

from types import SimpleNamespace

from sgt.core.lens import _load_backfill_state, _load_witnesses, _ref_key, get
from sgt.intent import group, theme
from sgt.store.gitbind import GitBinding, init_store


class _FakeResponses:
    def __init__(self, output_parsed):
        self._output_parsed = output_parsed

    def parse(self, **kwargs):
        return SimpleNamespace(
            output_parsed=self._output_parsed, usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class FakeClient:
    def __init__(self, output_parsed):
        self.responses = _FakeResponses(output_parsed)


def _no_client(*args, **kwargs):
    raise RuntimeError("OPENAI_API_KEY not found in environment or .env")


def _two_scope_commits(tmp_path):
    """Two same-scope commits with a real reference edge between them (bar calls foo) -- so
    structural gating (U3) merges them into one bundle, same as before that gating existed."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("fix(auth): add foo")
    (tmp_path / "b.py").write_text(
        "from a import foo\n\n\ndef bar():\n    return foo() + 1\n", encoding="utf-8",
    )
    gb.commit_all("fix(auth): add bar")
    get(tmp_path)


def test_no_op_ids_anywhere_in_schema_or_persisted_themes(tmp_path, monkeypatch):
    _two_scope_commits(tmp_path)
    monkeypatch.setattr(theme, "get_client", _no_client)

    themes = theme.build_themes(tmp_path)

    from sgt.core.store import Store

    real_op_ids = {op.id for op in Store(tmp_path).all_ops()}
    for t in themes.values():
        for sha in t["atom_shas"]:
            assert sha not in real_op_ids  # a commit sha, never an op-id
        assert "op_ids" not in t


def test_fallback_scope_themes_exist_with_zero_network(tmp_path, monkeypatch):
    _two_scope_commits(tmp_path)
    monkeypatch.setattr(theme, "get_client", _no_client)

    themes = theme.build_themes(tmp_path)

    assert len(themes) == 1
    (t,) = themes.values()
    assert t["label"] == "auth"
    assert t["source"] == "fallback"
    assert len(t["atom_shas"]) == 2


def test_fallback_entry_is_upgraded_once_a_client_becomes_available(tmp_path, monkeypatch):
    _two_scope_commits(tmp_path)
    monkeypatch.setattr(theme, "get_client", _no_client)
    theme.build_themes(tmp_path)

    fake = FakeClient(theme.ThemeLabel(label="Auth Bugfix", rationale="Fixes the auth flow."))
    monkeypatch.setattr(theme, "get_client", lambda repo: fake)
    themes = theme.build_themes(tmp_path)

    (t,) = themes.values()
    assert t["label"] == "Auth Bugfix"
    assert t["source"] == "llm"


def test_cache_hit_makes_zero_live_calls_on_second_build(tmp_path, monkeypatch):
    _two_scope_commits(tmp_path)
    fake = FakeClient(theme.ThemeLabel(label="Auth Bugfix", rationale="Fixes the auth flow."))
    monkeypatch.setattr(theme, "get_client", lambda repo: fake)

    first = theme.build_themes(tmp_path)

    from sgt.core.store import Store

    themer = theme.IntentThemer(tmp_path)
    bundles = group.scope_bundles(group.atoms(tmp_path), Store(tmp_path).all_ops())
    themer.label_bundle(bundles[0])
    assert themer.calls == 0  # cache hit -- the label was already persisted as "llm"

    second = theme.build_themes(tmp_path)
    assert first == second


def test_determinism_same_partition_and_cache_yields_byte_identical_themes(tmp_path, monkeypatch):
    _two_scope_commits(tmp_path)
    monkeypatch.setattr(theme, "get_client", _no_client)

    first = theme.build_themes(tmp_path)
    second = theme.build_themes(tmp_path)
    assert first == second


def test_subset_validation_drops_a_hallucinated_sha(tmp_path, monkeypatch):
    """A scope-less atom coalescing call that names a sha never shown to it must not have that
    sha survive into the persisted group."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("touch a.py")  # no conventional-commit scope -> scope-less atom
    get(tmp_path)

    real_atom = group.atoms(tmp_path)[0]
    hallucinated_group = theme.ThemeGroup(
        label="Bogus", rationale="made up", atom_shas=["ffffffff", real_atom.commit_sha[:8]],
    )
    fake = FakeClient(theme.ThemeGroups(groups=[hallucinated_group]))
    monkeypatch.setattr(theme, "get_client", lambda repo: fake)

    themer = theme.IntentThemer(tmp_path)
    result = themer.group_scopeless([real_atom])

    all_shas = {sha for g in result for sha in g.atom_shas}
    assert real_atom.commit_sha in all_shas
    assert "ffffffff" not in all_shas
    assert all(len(sha) == 40 for sha in all_shas)  # only real, full-length shas survive


def test_scopeless_atom_with_no_client_becomes_a_singleton_theme(tmp_path, monkeypatch):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("touch a.py")
    get(tmp_path)
    monkeypatch.setattr(theme, "get_client", _no_client)

    themes = theme.build_themes(tmp_path)

    assert len(themes) == 1
    (t,) = themes.values()
    assert t["source"] == "fallback"
    assert len(t["atom_shas"]) == 1


# -- U6: group_scopeless dedup + chunking (R7/R8) ------------------------------------------------


def _sync_fully(tmp_path) -> None:
    """`get()` now mines a fresh ref in deadline-bounded chunks (U1-U4) rather than genesis-to-HEAD
    in one call -- poll it the way a real bounded-timeout client would, until the ref's witness
    reaches HEAD and any backward genesis-backfill has finished."""
    gb = GitBinding(tmp_path)
    for _ in range(50):
        get(tmp_path)
        key = _ref_key(gb) or gb.head()
        witness = _load_witnesses(tmp_path).get(key)
        backfill = _load_backfill_state(tmp_path).get(key)
        if witness == gb.head() and (backfill is None or backfill.get("reached_genesis")):
            return
    raise AssertionError("ref did not reach full sync within 50 get() calls")


def _scopeless_atoms(tmp_path, n: int) -> list:
    gb, _ = init_store(tmp_path)
    for i in range(n):
        (tmp_path / f"f{i}.py").write_text(f"def fn{i}():\n    return {i}\n", encoding="utf-8")
        gb.commit_all(f"touch f{i}.py")  # no scope -> every atom is scope-less
    _sync_fully(tmp_path)
    return group.atoms(tmp_path)


def test_group_scopeless_first_group_wins_on_an_overlapping_llm_response(tmp_path, monkeypatch):
    """A crafted LLM response naming the same sha in two groups must not let that atom land in
    both persisted themes -- the earlier group (in the LLM's own returned order) keeps it."""
    atoms = _scopeless_atoms(tmp_path, 3)
    shas = sorted(a.commit_sha for a in atoms)

    overlapping = theme.ThemeGroups(groups=[
        theme.ThemeGroup(label="Group A", rationale="first", atom_shas=[shas[0][:8], shas[1][:8]]),
        theme.ThemeGroup(label="Group B", rationale="second", atom_shas=[shas[1][:8], shas[2][:8]]),
    ])
    fake = FakeClient(overlapping)
    monkeypatch.setattr(theme, "get_client", lambda repo: fake)

    themer = theme.IntentThemer(tmp_path)
    result = themer.group_scopeless(atoms)

    by_label = {g.label: set(g.atom_shas) for g in result}
    assert by_label["Group A"] == {shas[0], shas[1]}
    assert by_label["Group B"] == {shas[2]}  # shas[1] already claimed by Group A -- dropped here
    all_shas = [sha for g in result for sha in g.atom_shas]
    assert sorted(all_shas) == shas  # every atom still lands in exactly one group, none lost


def test_group_scopeless_chunks_a_backlog_larger_than_max_atoms(tmp_path, monkeypatch):
    """45 scope-less atoms (> MAX_ATOMS=40) must all get a theme -- regression for the review's
    exact repro, which previously landed only the first 40 via `[:MAX_ATOMS]` truncation."""
    monkeypatch.setattr(theme, "get_client", _no_client)
    atoms = _scopeless_atoms(tmp_path, 45)

    themer = theme.IntentThemer(tmp_path)
    result = themer.group_scopeless(atoms)

    all_shas = {sha for g in result for sha in g.atom_shas}
    assert all_shas == {a.commit_sha for a in atoms}


def test_group_scopeless_unchanged_chunk_hits_cache_changed_chunk_does_not(tmp_path, monkeypatch):
    fake = FakeClient(theme.ThemeGroups(groups=[]))
    monkeypatch.setattr(theme, "get_client", lambda repo: fake)
    atoms = _scopeless_atoms(tmp_path, 45)  # two chunks: 40 + 5

    themer = theme.IntentThemer(tmp_path)
    themer.group_scopeless(atoms)
    assert themer.calls == 2  # one live call per chunk
    themer.save()

    # rebuild with the exact same atoms -- both chunks are cache hits
    themer2 = theme.IntentThemer(tmp_path)
    themer2.group_scopeless(atoms)
    assert themer2.calls == 0

    # a 46th scope-less atom only changes the second (smaller) chunk's content hash
    from sgt.store.gitbind import GitBinding

    (tmp_path / "f45.py").write_text("def fn45():\n    return 45\n", encoding="utf-8")
    GitBinding(tmp_path).commit_all("touch f45.py")
    get(tmp_path)
    grown = group.atoms(tmp_path)

    themer3 = theme.IntentThemer(tmp_path)
    themer3.group_scopeless(grown)
    assert themer3.calls == 1  # only the changed (second) chunk re-requests
