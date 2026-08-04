"""The pure "so-what" layer (`sgt.api.so_what_for`): the one-line consequence a mutating verb's
consequence pane leads with. These pin the exact sentences over hand-built projection dicts -- no
store, no repo -- since the function is pure over the projection + the caller's kept-set. The
projection *shape* (fallout/carry_count/reversible over a real corpus) is covered in
`tests/test_api.py`; here we pin the wording and the kept-set arithmetic."""

from sgt.api import so_what_for


def _revert(symbol="a.py::foo", blast_ids=(), **extra):
    return {
        "verb": "revert", "ok": True, "forked": False, "message": "",
        "affected_symbols": [symbol],
        "fallout": [{"kind": "blast", "op_id": oid} for oid in blast_ids],
        **extra,
    }


def test_clean_revert_names_the_symbol_and_that_nothing_depends_on_it():
    assert so_what_for(_revert()) == (
        "Removes a.py::foo. Nothing depends on it — clean revert. Undo-able."
    )


def test_revert_with_dependents_says_how_many_will_break():
    assert so_what_for(_revert(blast_ids=("op1", "op2"))) == (
        "a.py::foo will break — 2 dependent(s) to re-draft. Undo-able."
    )


def test_keeping_a_dependent_moves_it_out_of_the_break_count():
    pview = _revert(blast_ids=("op1", "op2"))
    assert so_what_for(pview, kept=frozenset({"op1"})) == (
        "a.py::foo will break — 1 dependent(s) to re-draft, keeping 1. Undo-able."
    )


def test_keeping_every_dependent_drops_the_break_count_to_zero():
    pview = _revert(blast_ids=("op1", "op2"))
    assert so_what_for(pview, kept=frozenset({"op1", "op2"})) == (
        "a.py::foo will break — 0 dependent(s) to re-draft, keeping 2. Undo-able."
    )


def test_a_stale_kept_id_that_is_not_a_blast_row_is_ignored():
    pview = _revert(blast_ids=("op1",))
    assert so_what_for(pview, kept=frozenset({"not-a-blast"})) == (
        "a.py::foo will break — 1 dependent(s) to re-draft. Undo-able."
    )


def test_a_forked_refusal_points_at_resolve():
    pview = _revert(ok=False, forked=True)
    assert so_what_for(pview) == (
        "Won't apply — revert of a.py::foo would fork it. "
        "Resolve the fork first (sgt resolve a.py::foo)."
    )


def test_a_plain_refusal_surfaces_the_message():
    pview = _revert(ok=False, message="target not in the ideal")
    assert so_what_for(pview) == "Won't apply — target not in the ideal."


def test_restore_says_the_symbol_and_prerequisites_return():
    pview = {"verb": "restore", "ok": True, "forked": False,
             "affected_symbols": ["a.py::foo"], "fallout": []}
    assert so_what_for(pview) == (
        "Re-adds a.py::foo and its prerequisites. Nothing to reconcile. Undo-able."
    )


def test_metadata_reorg_verbs_say_code_is_untouched():
    pview = {"verb": "merge", "ok": True, "forked": False,
             "affected_symbols": ["auth"], "fallout": []}
    assert so_what_for(pview) == "Merges into auth — metadata only, code untouched. Undo-able."


def test_each_reorg_verb_has_its_own_metadata_phrasing():
    def meta(verb):
        return so_what_for({"verb": verb, "ok": True, "forked": False,
                            "affected_symbols": ["auth"], "fallout": []})

    assert meta("split") == "Splits auth — metadata only, code untouched. Undo-able."
    assert meta("rename") == "Relabels to auth — metadata only, code untouched. Undo-able."
    assert meta("move") == "Moves ops onto auth — metadata only, code untouched. Undo-able."


def _land(target="main", ops_added=0, forks=(), oracle_configured=True, clean=True, error=None):
    return {
        "verb": "land", "target": target, "ok": clean and not forks and oracle_configured,
        "forked": bool(forks), "affected_symbols": [f[0] for f in forks], "fallout": [],
        "ops_added": ops_added, "forks": [list(f) for f in forks],
        "oracle_configured": oracle_configured, "clean": clean, "error": error,
    }


def test_a_clean_land_says_how_many_ops_advance_and_that_it_is_one_way():
    # `land` advances shared state -- the escape clause flips to "not auto-undoable".
    assert so_what_for(_land(ops_added=3)) == (
        "Advances main by 3 op — runs the oracle (tests) then CAS. "
        "Not auto-undoable — review carefully."
    )


def test_a_forked_land_names_the_blocker_and_the_resolve_remedy():
    pview = _land(ops_added=0, forks=[("api::route", "0ee9a65f11", "5e6eaf5822")])
    assert so_what_for(pview) == (
        "Won't advance main — 1 fork(s) block it. Resolve first: sgt resolve api::route."
    )


def test_multiple_forks_count_the_extras():
    pview = _land(forks=[("a::x", "aaaa1111", "bbbb2222"), ("b::y", "cccc3333", "dddd4444")])
    assert so_what_for(pview) == (
        "Won't advance main — 2 fork(s) block it (+1 more). "
        "Resolve first: sgt resolve a::x."
    )


def test_a_land_with_no_oracle_says_law_g_refuses():
    assert so_what_for(_land(ops_added=2, oracle_configured=False)) == (
        "Won't advance main — no oracle configured; land refuses an unverified op-set (LAW-G)."
    )


def test_an_unclean_land_reports_why_it_cannot_land_yet():
    assert so_what_for(_land(clean=False, error="working tree not clean")) == (
        "Can't land onto main yet — working tree not clean."
    )


def test_primary_falls_back_to_target_when_no_symbol_resolved():
    pview = {"verb": "revert", "ok": True, "forked": False,
             "affected_symbols": [], "target": "f-1a2b", "fallout": []}
    assert so_what_for(pview).startswith("Removes f-1a2b.")


def test_primary_names_the_target_not_the_alphabetically_first_dependent():
    # Regression: `affected_symbols` is the sorted up-set closure, so its first entry is an
    # arbitrary dependent (e.g. `Element`), not what the user reverted. The consequence must lead
    # with the *target* (`RGA._flush`) -- reverting a method should not read "Element will break".
    pview = {
        "verb": "revert", "ok": True, "forked": False,
        "target": "crdt.py::RGA._flush",
        "affected_symbols": ["crdt.py::Element", "crdt.py::RGA", "crdt.py::RGA._flush"],
        "fallout": [{"kind": "blast", "op_id": "op1"}],
    }
    assert so_what_for(pview) == "crdt.py::RGA._flush will break — 1 dependent(s) to re-draft. Undo-able."


def _sync(remote="origin", branch="main", ops_added=0, forks=(), up_to_date=False):
    return {
        "verb": "sync", "remote": remote, "target": branch,
        "up_to_date": up_to_date, "ops_added": ops_added,
        "forks": [list(f) for f in forks], "reversible": False,
    }


def test_a_clean_sync_says_the_op_count_is_fork_free_and_one_way():
    # `sync` advances the *local* branch (ungated), so it too is not auto-undoable.
    assert so_what_for(_sync(ops_added=4)) == (
        "Folds in 4 op(s) from origin/main — footprint-disjoint, no forks. "
        "Not auto-undoable — review carefully."
    )


def test_a_forked_sync_says_work_is_not_lost_and_the_fork_waits():
    pview = _sync(ops_added=2, forks=[("api::route", "0ee9a65f11", "5e6eaf5822")])
    assert so_what_for(pview) == (
        "Folds in 2 op(s) from origin/main; 1 fork(s) surface for you to resolve — "
        "no work is lost, they wait at the common ancestor. Not auto-undoable — review carefully."
    )


def test_an_up_to_date_sync_says_there_is_nothing_to_fold_in():
    assert so_what_for(_sync(up_to_date=True)) == (
        "Already up to date with origin/main — nothing to fold in."
    )


def _resolve(symbol="api.py::route", clean=True, error=None):
    return {"verb": "resolve", "target": symbol, "clean": clean,
            "error": error, "reversible": False}


def test_a_clean_resolve_spells_out_the_three_step_remedy():
    assert so_what_for(_resolve()) == (
        "Resolves the fork on api.py::route: fulfills your merged edit, runs the oracle, "
        "then lands it (closes the fork). Not auto-undoable — review carefully."
    )


def test_a_resolve_with_no_draft_reports_why_it_cannot_run_yet():
    pview = _resolve(clean=False, error="no drafted reconciliation")
    assert so_what_for(pview) == (
        "Can't resolve api.py::route yet — no drafted reconciliation."
    )
