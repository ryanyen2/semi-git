"""Characterization golden master for the `ugh` **CLI surface** (plan C11, U16).

`tests/golden/test_golden.py` freezes the `ugh.api` projection (the kernel read views). This file
freezes the porcelain one layer up: for every `ugh` verb it captures the exit code, the
human-readable text, and the `--json` output (where the verb emits it) against pinned-SHA fixtures.
Two things are being locked:

  1. **C11 -- byte-identical through the CLI migration.** U18 rewrites the hand-rolled `_strip_opt`
     parser as an argparse package; these snapshots are the contract that its text and (especially)
     `--json` output do not drift for any pre-existing verb. `--json` is the machine surface the
     VSCode extension consumes (R21), so its byte-stability is the one that must not move.
  2. **Determinism, proven end to end.** Commit shas appear verbatim in every address; because
     these fixtures are byte-identical across independent builds, the addresses -- and therefore
     this whole snapshot -- are stable.

Regenerate after an intentional, reviewed CLI change:

    UGH_UPDATE_GOLDEN=1 uv run pytest tests/golden/test_cli_golden.py -q

then review the snapshot diff before committing. Same hermetic discipline as the rest of the
golden/laws suites: real ``git``, no network, no wall-clock. `ugh mcp` is the one verb deliberately not captured -- it is a long-lived stdio
server, not a request/response command.
"""

from __future__ import annotations

import contextlib
import difflib
import io
import json
import os
import pathlib
import re

import pytest

from ugh import cli
from ugh.core.lens import get
from tests.laws import corpus

_SNAPSHOTS = pathlib.Path(__file__).resolve().parent / "snapshots"


def _capture(repo: pathlib.Path, argv: list[str]) -> dict:
    """Run `ugh <argv>` with `repo` as cwd (the CLI operates on the working directory, like every
    real invocation), capturing exit code and stdout. The fixtures pin their commit shas, so the
    captured bytes are stable across runs."""
    cwd = os.getcwd()
    os.chdir(repo)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
    finally:
        os.chdir(cwd)
    return {"argv": list(argv), "exit": code, "out": buf.getvalue()}


def _both(repo: pathlib.Path, argv: list[str]) -> dict:
    """Capture a verb's text form and its `--json` form (the two surfaces C11 pins)."""
    return {"text": _capture(repo, argv), "json": _capture(repo, [*argv, "--json"])}


def capture_cli_surface(root: str) -> dict:
    """Build fresh fixtures and capture every `ugh` verb. A verb that mutates committed/staged
    state captures its text and its `--json` form on two *separate* fresh fixtures (running it
    twice on one repo would see already-applied state); read verbs share one mined repo,
    since re-running a read verb is idempotent."""
    root = pathlib.Path(root)
    views: dict[str, dict] = {}
    seq = [0]

    def fresh(case: str = "linear_history", prep: list[str] | None = None) -> pathlib.Path:
        seq[0] += 1
        repo = corpus.CORPUS[case].build(root / f"f{seq[0]}")
        get(repo)
        if prep:
            _capture(repo, prep)
        return repo

    def both_isolated(argv_fn, **kw) -> dict:
        """A mutating verb: text and `--json` each on their own fresh (identically-built, so
        deterministic-id) fixture. `argv_fn(repo)` derives any op/feature ids from the fixture."""
        rt, rj = fresh(**kw), fresh(**kw)
        return {"text": _capture(rt, argv_fn(rt)), "json": _capture(rj, [*argv_fn(rj), "--json"])}

    # -- read surface (one mined linear_history repo; read verbs are idempotent) ----------------
    rf = fresh()
    views["help"] = _capture(rf, ["help"])
    for key, argv in (("log", ["log"]), ("status", ["status"]), ("check", ["check"]),
                      ("show", ["show", "c.py::qux"]), ("find", ["find", "qux"]),
                      ("plan_status", ["plan"])):
        views[key] = _both(rf, argv)
    views["revert_emit"] = _both(fresh(), ["revert", "--emit", "c.py::qux"])  # --emit writes nothing

    # -- deterministic error / refusal surfaces (fail before mutating; safe to re-run) ----------
    views["revert_unknown"] = _both(fresh(), ["revert", "nope::nothing"])
    # A genuinely dirty tree: since Phase 1.2 gitignores `.sgt/`, a fresh repo's tree is clean and
    # `sync` would sail past the guard into a real fetch -- so dirty a *tracked* source file to
    # exercise the clean-tree refusal the view is named for.
    dirty_sync = fresh()
    (dirty_sync / "a.py").write_text("def foo():\n    return 999\n", encoding="utf-8")
    views["sync_refuses_dirty_tree"] = _both(dirty_sync, ["sync"])  # dirty tracked file -> clean-tree guard

    # -- mutating verbs (text + --json each on its own fresh fixture) ---------------------------
    # `--yes` skips the feedforward confirm gate so the mutation actually applies (a non-tty capture
    # would otherwise draw the preview and refuse, exit 2). The gate itself is covered in
    # tests/cli/test_revert.py; here we freeze the applied surface.
    views["revert"] = both_isolated(lambda r: ["revert", "c.py::qux", "--yes"])
    views["restore"] = {
        k: _redact_bookkeeping_sha(v) for k, v in both_isolated(
            lambda r: ["restore", "c.py::qux", "--yes"],
            prep=["revert", "c.py::qux", "--yes"]).items()}
    views["name"] = both_isolated(lambda r: ["name", "c.py::qux", "Qux"])
    views["rename"] = both_isolated(lambda r: ["rename", "Qux", "Quux"], prep=["name", "c.py::qux", "Qux"])

    # -- porcelain daily-loop verbs (U26/D3): save/undo --------------------------------------------

    def _dirty_repo() -> pathlib.Path:
        seq[0] += 1
        repo = corpus.CORPUS["linear_history"].build(root / f"f{seq[0]}")
        get(repo)
        (repo / "new_file.py").write_text("def new_thing():\n    return 1\n", encoding="utf-8")
        return repo

    views["save"] = {
        "text": _redact_witness_sha(_capture(_dirty_repo(), ["save", "-m", "add new_file", "--as", "new"])),
        "json": _redact_witness_sha(_capture(_dirty_repo(), ["save", "-m", "add new_file", "--as", "new", "--json"])),
    }
    views["save_nothing_to_save"] = _both(fresh(), ["save"])

    def _undoable_repo() -> pathlib.Path:
        seq[0] += 1
        repo = corpus.CORPUS["linear_history"].build(root / f"f{seq[0]}")
        get(repo)
        _capture(repo, ["revert", "c.py::qux", "--yes"])  # log one take-out commit to undo
        return repo

    views["undo"] = {
        "text": _redact_bookkeeping_sha(_capture(_undoable_repo(), ["undo"])),
        "json": _redact_bookkeeping_sha(_capture(_undoable_repo(), ["undo", "--json"])),
    }
    views["undo_nothing_to_undo"] = _both(fresh(), ["undo"])

    # -- init on a fresh, un-mined git repo -----------------------------------------------------
    plain = corpus.CORPUS["mixed_coverage"].build(root / "init")
    views["init"] = _capture(plain, ["init", "."])

    # -- usage / unknown-verb fall-throughs -----------------------------------------------------
    views["unknown_verb"] = _capture(rf, ["not-a-verb"])

    return views


def _redact(capture: dict, shas) -> dict:
    out = capture["out"]
    for sha in shas:
        out = re.sub(rf"\b{sha}(?:[0-9a-f]{{33}})?\b", "<witness-sha>", out)
    return {**capture, "out": out}


def _redact_witness_sha(capture: dict) -> dict:
    """A save's own commit is made with the real wall-clock date (no pinned timestamp, unlike the
    corpus fixtures), so its sha -- in its header and in every step's address -- is not
    reproducible across runs: freeze a placeholder instead of the literal value."""
    out = capture["out"]
    sha = json.loads(out).get("commit") if out.startswith("{") else re.findall(r"save ([0-9a-f]{7})", out)[:1]
    return _redact(capture, [sha[:7]] if isinstance(sha, str) else sha)


_BOOKKEEPING_SHA_RE = re.compile(r'([0-9a-f]{7})(?:" ?,?\s*(?:"subject": )?"ugh |\s"ugh )')


def _redact_bookkeeping_sha(capture: dict) -> dict:
    """A restore or an undo names the commit that took the step out: ugh's own commit, made with
    the real wall-clock date, so its sha is not reproducible across runs."""
    return _redact(capture, set(_BOOKKEEPING_SHA_RE.findall(capture["out"])))


def _dump(views: dict) -> str:
    return json.dumps(views, indent=2, sort_keys=True) + "\n"


def _assert_matches_golden(snapshot_name: str, actual: str) -> None:
    snapshot = _SNAPSHOTS / snapshot_name
    if os.environ.get("UGH_UPDATE_GOLDEN"):
        snapshot.write_text(actual, encoding="utf-8")
        pytest.skip(f"updated golden snapshot {snapshot.name}")
    assert snapshot.exists(), f"missing golden {snapshot} -- regenerate with UGH_UPDATE_GOLDEN=1"
    expected = snapshot.read_text(encoding="utf-8")
    if actual != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(), actual.splitlines(),
                fromfile=f"{snapshot_name} (golden)", tofile=f"{snapshot_name} (actual)", lineterm="",
            )
        )
        pytest.fail(f"ugh CLI surface drifted for {snapshot_name!r}:\n{diff}")


def test_cli_surface_matches_golden(tmp_path):
    """Every `ugh` verb's text + `--json` output, captured against pinned fixtures (C11)."""
    _assert_matches_golden("cli_surface.json", _dump(capture_cli_surface(str(tmp_path))))
