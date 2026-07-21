"""Tests for sgt.repair.loop -- the semantic repair loop (plan U5/U6).

`FakeBackend` scripts one image per call, indexed by total calls so far (not
`RepairRequest.attempt`, which restarts at 1 every oracle round) -- it stands in for a real LLM
backend deterministically. The fixture is a same-file, separate-commit helper/user pair (see
`test_rewrite.py`'s comment on why separate commits matter: touched together in one commit,
def-use untangling would fold them into a single op, leaving no cross-op reference edge to
draft a hollow for at all) -- same-file specifically to sidestep the unrelated, documented gap
where a dependent's module-level import lives in a separate residue entity a single-symbol hollow
fix can't touch (see FINDINGS.md).
"""

from __future__ import annotations

import json
from pathlib import Path

from sgt.core import mine, order, rewrite
from sgt.core.lens import get
from sgt.core.op import make_op
from sgt.core.rewrite import RewriteDraft
from sgt.core.store import Store
from sgt.repair.backends import RepairBackend, RepairProposal
from sgt.repair.loop import repair
from sgt.store.gitbind import init_store


def _configure_oracle(repo: Path, tiers: list[tuple[str, str]]) -> None:
    payload = {"tiers": [{"name": name, "command": command} for name, command in tiers]}
    (repo / ".sgt").mkdir(exist_ok=True)
    (repo / ".sgt" / "oracle.json").write_text(json.dumps(payload), encoding="utf-8")


def _fixture(repo: Path):
    gb, _ = init_store(repo)
    (repo / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add helper")
    (repo / "m.py").write_text(
        "def helper():\n    return 1\n\n\ndef user():\n    return helper() + 1\n", encoding="utf-8"
    )
    gb.commit_all("add user, depending on helper")
    get(repo)
    ops = Store(repo).all_ops()
    helper_op = next(o for o in ops if "m.py::helper" in o.footprint)
    user_op = next(o for o in ops if "m.py::user" in o.footprint)
    assert (helper_op.id, user_op.id) in order.reference_edges(ops)  # sanity: a real dependency
    return helper_op, user_op


def _fixture_transitive(repo: Path):
    """helper <- user <- caller, where caller calls only user, never helper directly (U7)."""
    gb, _ = init_store(repo)
    (repo / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add helper")
    (repo / "m.py").write_text(
        "def helper():\n    return 1\n\n\ndef user():\n    return helper() + 1\n", encoding="utf-8"
    )
    gb.commit_all("add user, depending on helper")
    (repo / "m.py").write_text(
        "def helper():\n    return 1\n\n\ndef user():\n    return helper() + 1\n\n\n"
        "def caller():\n    return user() + 1\n",
        encoding="utf-8",
    )
    gb.commit_all("add caller, depending on user (not helper)")
    get(repo)
    ops = Store(repo).all_ops()
    helper_op = next(o for o in ops if "m.py::helper" in o.footprint)
    user_op = next(o for o in ops if "m.py::user" in o.footprint)
    caller_op = next(o for o in ops if "m.py::caller" in o.footprint)
    assert (helper_op.id, user_op.id) in order.reference_edges(ops)
    assert (user_op.id, caller_op.id) in order.reference_edges(ops)
    return helper_op, user_op, caller_op


class FakeBackend(RepairBackend):
    """Returns `images[calls so far]`, clamped to the last entry once exhausted."""

    def __init__(self, images: list[bytes]):
        self.images = images
        self.calls = 0

    def propose(self, request):
        idx = min(self.calls, len(self.images) - 1)
        self.calls += 1
        return RepairProposal(image=self.images[idx].decode("utf-8"), rationale="fake")


def test_happy_path_lands_and_attributes(tmp_path):
    repo = tmp_path / "repo"
    helper_op, user_op = _fixture(repo)
    _configure_oracle(repo, [("py_compile", "python -m py_compile m.py")])
    draft = rewrite.revert_keep_dependents(repo, helper_op.id)

    backend = FakeBackend([b"def user():\n    return 99"])
    result = repair(repo, draft, backend, plan="test-plan")

    assert result.ok, result.message
    assert result.attempts == 1
    assert result.oracle_rounds == 1
    assert backend.calls == 1

    ops = Store(repo).all_ops()
    fulfilled = next(o for o in ops if "m.py::user" in o.footprint and o.id != user_op.id)
    assert fulfilled.images["m.py::user"] == b"def user():\n    return 99"
    assert fulfilled.provenance == (result.sha,)
    assert len(fulfilled.attribution) == 1
    assert fulfilled.attribution[0].sha == result.sha
    assert fulfilled.attribution[0].agent == "integration"
    assert fulfilled.attribution[0].plan == "test-plan"

    ideal = get(repo)
    assert "m.py::helper" not in ideal.frontier(ops)


def test_transitive_dependent_survives_without_costing_a_backend_call(tmp_path):
    """U7: `caller` is two hops from `helper` (it calls `user`, never `helper`). The loop must not
    burn a backend call fixing it -- its content is carried forward unchanged by
    `rewrite.build_candidate` before the repair loop ever sees it, so `backend.calls` stays exactly
    what the *direct* dependent (`user`) alone needs."""
    repo = tmp_path / "repo"
    helper_op, user_op, caller_op = _fixture_transitive(repo)
    _configure_oracle(repo, [("py_compile", "python -m py_compile m.py")])
    draft = rewrite.revert_keep_dependents(repo, helper_op.id)
    assert len(draft.hollow_ids) == 1  # only `user` -- `caller` is carried, not hollowed
    assert draft.meta["carry_forward"] == ["m.py::caller"]

    backend = FakeBackend([b"def user():\n    return 99"])
    result = repair(repo, draft, backend)

    assert result.ok, result.message
    assert backend.calls == 1  # the transitive tail cost nothing

    ops = Store(repo).all_ops()
    assert "m.py::helper" not in get(repo).frontier(ops)
    carried = next(o for o in ops if "m.py::caller" in o.footprint and o.id != caller_op.id)
    assert carried.images["m.py::caller"] == caller_op.images["m.py::caller"]


def test_repoint_only_edit_lands_with_zero_model_calls_and_no_integration_provenance(tmp_path):
    """U5/R6 (behavioral + integration): a purely mechanical repair -- a repoint with *no* hollow --
    lands without the backend ever being called (`propose` count == hollow count == 0), and the
    repointed op is witnessed by the landing commit yet carries no LLM/integration attribution,
    unlike a fulfilled hollow (`test_happy_path_lands_and_attributes`)."""
    repo = tmp_path / "repo"
    helper_op, user_op = _fixture(repo)
    _configure_oracle(repo, [("py_compile", "python -m py_compile m.py")])

    helper_sym = "m.py::helper"
    v1 = helper_op.footprint[helper_sym][1]
    assert (helper_sym, v1) in user_op.requires

    # `edit helper` advances it to a new version; `user`'s bytes are unchanged, only its edge is stale.
    new_bytes = b"def helper():\n    return 2"
    v2 = mine._positional_version(helper_sym, mine._content_version(new_bytes))
    helper2 = make_op({helper_sym: (v1, v2)}, {helper_sym: new_bytes}, kind="extend")
    Store(repo).add(helper2)

    draft = RewriteDraft(
        ok=True, verb="edit", target=helper_op.id,
        meta={
            "removed_ids": [user_op.id],
            "required_ids": [helper2.id],
            "repoint": [{"op_id": user_op.id, "symbol": helper_sym,
                         "old_version": v1, "new_version": v2}],
        },
    )
    backend = FakeBackend([b"UNUSED -- the model must never be called for a mechanical repoint"])
    result = repair(repo, draft, backend)

    assert result.ok, result.message
    assert backend.calls == 0  # no hollow remained after mechanical repair -> zero model calls
    assert result.oracle_rounds == 1

    ops = Store(repo).all_ops()
    repointed = next(o for o in ops if o.kind == "repoint" and "m.py::user" in o.footprint)
    assert repointed.images["m.py::user"] == user_op.images["m.py::user"]  # byte-identical
    assert (helper_sym, v2) in repointed.requires and (helper_sym, v1) not in repointed.requires
    assert result.sha in repointed.provenance  # witnessed by the landing commit...
    assert all(a.agent != "integration" for a in repointed.attribution)  # ...but not LLM-attributed


def test_tier0_reject_then_recover(tmp_path):
    """A first proposal that still calls the removed symbol is rejected by Tier-0 for free (no
    `stage` call); the backend's second, corrected proposal lands."""
    repo = tmp_path / "repo"
    helper_op, user_op = _fixture(repo)
    _configure_oracle(repo, [("py_compile", "python -m py_compile m.py")])
    draft = rewrite.revert_keep_dependents(repo, helper_op.id)

    backend = FakeBackend([b"def user():\n    return helper() + 1", b"def user():\n    return 42"])
    result = repair(repo, draft, backend)

    assert result.ok, result.message
    assert result.attempts == 2
    assert result.oracle_rounds == 1


def test_stuck_detector_stops_on_repeated_rejection(tmp_path):
    """A backend that keeps returning the same rejected image is cut off before burning its whole
    attempt budget -- and the working tree is left clean, since `stage` was never called."""
    repo = tmp_path / "repo"
    helper_op, user_op = _fixture(repo)
    _configure_oracle(repo, [("py_compile", "python -m py_compile m.py")])
    draft = rewrite.revert_keep_dependents(repo, helper_op.id)

    backend = FakeBackend([b"def user():\n    return helper() + 1"])
    result = repair(repo, draft, backend, max_attempts=4)

    assert not result.ok
    assert backend.calls == 2  # first attempt, then the repeat that trips the detector
    assert not (repo / ".sgt" / "staged.json").exists()


def test_oracle_round_fails_unstages_and_redrafts_before_landing(tmp_path):
    """A Tier-0-passing proposal that still fails the real oracle triggers `unstage` + a fresh
    draft (deterministic content-addressed ids) rather than landing a broken candidate; the
    backend's next attempt, now correct, lands on the second oracle round."""
    repo = tmp_path / "repo"
    helper_op, user_op = _fixture(repo)
    (repo / "test_m.py").write_text("import m\n\ndef test_user():\n    assert m.user() == 99\n", encoding="utf-8")
    _configure_oracle(repo, [
        ("py_compile", "python -m py_compile m.py"),
        ("pytest", "python -m pytest test_m.py -q"),
    ])
    draft = rewrite.revert_keep_dependents(repo, helper_op.id)

    backend = FakeBackend([b"def user():\n    return 100", b"def user():\n    return 99"])
    result = repair(repo, draft, backend, max_attempts=4, max_oracle_rounds=2)

    assert result.ok, result.message
    assert result.oracle_rounds == 2
    assert not (repo / ".sgt" / "staged.json").exists()


def test_oracle_exhausted_returns_ok_false_and_clean_tree(tmp_path):
    """Every oracle round staying red is a real failure -- `land`'s gate is never bypassed -- and
    the last round's `unstage` already ran, so nothing is left staged."""
    repo = tmp_path / "repo"
    helper_op, user_op = _fixture(repo)
    (repo / "test_m.py").write_text("import m\n\ndef test_user():\n    assert m.user() == 99\n", encoding="utf-8")
    _configure_oracle(repo, [
        ("py_compile", "python -m py_compile m.py"),
        ("pytest", "python -m pytest test_m.py -q"),
    ])
    draft = rewrite.revert_keep_dependents(repo, helper_op.id)

    backend = FakeBackend([b"def user():\n    return 100"])  # Tier-0-passing, never oracle-passing
    result = repair(repo, draft, backend, max_attempts=1, max_oracle_rounds=2)

    assert not result.ok
    assert result.message == "oracle verdict stayed red after all rounds"
    assert not (repo / ".sgt" / "staged.json").exists()
