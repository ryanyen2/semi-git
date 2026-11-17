"""The pure "so-what" layer (`ugh.api.so_what_for`): the one-line consequence a sync or resolve
preview leads with. These pin the exact sentences over hand-built projection dicts."""

from ugh.api import so_what_for


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
        "Resolves the fork on api.py::route: takes your merged edit, runs the oracle, "
        "then lands it (closes the fork). Not auto-undoable — review carefully."
    )


def test_a_resolve_with_no_open_fork_reports_why_it_cannot_run():
    pview = _resolve(clean=False, error="no open fork")
    assert so_what_for(pview) == (
        "Can't resolve api.py::route yet — no open fork."
    )
