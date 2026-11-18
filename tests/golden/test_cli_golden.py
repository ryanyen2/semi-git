"""Characterization golden master for the `ugh` **CLI surface** (plan C11, U16).

`tests/golden/test_golden.py` freezes the `ugh.api` projection (the kernel read views). This file
freezes the porcelain one layer up: for every `ugh` verb it captures the exit code, the
human-readable text, and the `--json` output (where the verb emits it) against pinned-SHA fixtures.
Two things are being locked:

  1. **C11 -- byte-identical through the CLI migration.** U18 rewrites the hand-rolled `_strip_opt`
     parser as an argparse package; these snapshots are the contract that its text and (especially)
     `--json` output do not drift for any pre-existing verb. `--json` is the machine surface the
     VSCode extension consumes (R21), so its byte-stability is the one that must not move.
  2. **Provenance exclusion, proven end to end.** Op ids appear verbatim in `log`/`map`/…
     output; because these fixtures are byte-identical across independent builds (provenance is
     excluded from `compute_id`), the ids -- and therefore this whole snapshot -- are stable. A
     later unit that adds structured provenance (U22) must keep every id here unchanged.

Regenerate after an intentional, reviewed CLI change:

    UGH_UPDATE_GOLDEN=1 uv run pytest tests/golden/test_cli_golden.py -q

then review the snapshot diff before committing. Same hermetic discipline as the rest of the
golden/laws suites: real ``git``, no network, no wall-clock, deterministic offline feature labels
(no API key). `ugh mcp` is the one verb deliberately not captured -- it is a long-lived stdio
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
from ugh.api import map_view
from ugh.core.lens import get
from tests.laws import corpus

_SNAPSHOTS = pathlib.Path(__file__).resolve().parent / "snapshots"


def _capture(repo: pathlib.Path, argv: list[str]) -> dict:
    """Run `ugh <argv>` with `repo` as cwd (the CLI operates on the working directory, like every
    real invocation), capturing exit code and stdout. Determinism: op ids are content-addressed,
    feature labels use the offline fallback, so the captured bytes are stable across runs."""
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


def _feature_ids(repo: pathlib.Path) -> list[str]:
    return [n["id"] for n in map_view(repo)["nodes"] if n.get("kind") == "feature"]


def capture_cli_surface(root: str) -> dict:
    """Build fresh fixtures and capture every `ugh` verb. A verb that mutates committed/staged
    state captures its text and its `--json` form on two *separate* fresh fixtures (running it
    twice on one repo would see already-applied state); read verbs share one mined+mapped repo,
    since re-running a read verb is idempotent."""
    root = pathlib.Path(root)
    views: dict[str, dict] = {}
    seq = [0]

    def fresh(case: str = "linear_history", prep: list[str] | None = None, mapped: bool = False) -> pathlib.Path:
        seq[0] += 1
        repo = corpus.CORPUS[case].build(root / f"f{seq[0]}")
        get(repo)
        if mapped:
            _capture(repo, ["log", "--refresh"])
        if prep:
            _capture(repo, prep)
        return repo

    def both_isolated(argv_fn, **kw) -> dict:
        """A mutating verb: text and `--json` each on their own fresh (identically-built, so
        deterministic-id) fixture. `argv_fn(repo)` derives any op/feature ids from the fixture."""
        rt, rj = fresh(**kw), fresh(**kw)
        return {"text": _capture(rt, argv_fn(rt)), "json": _capture(rj, [*argv_fn(rj), "--json"])}

    # -- read surface (one mined + mapped linear_history repo; read verbs are idempotent) -------
    rf = fresh(mapped=True)
    views["help"] = _capture(rf, ["help"])
    for key, argv in (("log", ["log"]), ("status", ["status"]), ("check", ["check"])):
        views[key] = _both(rf, argv)
    views["map"] = {"json": _capture(rf, ["log", "--tree", "--json"])}
    views["plan_status"] = _both(rf, ["plan", "status"])
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
    views["revert"] = both_isolated(lambda r: ["revert", "c.py::qux", "--yes"], mapped=True)
    views["restore"] = {
        k: _redact_bookkeeping_sha(v) for k, v in both_isolated(
            lambda r: ["restore", "c.py::qux", "--yes"],
            prep=["revert", "c.py::qux", "--yes"], mapped=True).items()}
    views["rename"] = both_isolated(
        lambda r: ["feature", "rename", _feature_ids(r)[0], "Renamed Feature"], mapped=True
    )

    # -- porcelain daily-loop verbs (U26/D3): save/undo --------------------------------------------

    def _dirty_repo() -> pathlib.Path:
        seq[0] += 1
        repo = corpus.CORPUS["linear_history"].build(root / f"f{seq[0]}")
        get(repo)
        (repo / "new_file.py").write_text("def new_thing():\n    return 1\n", encoding="utf-8")
        return repo

    views["save"] = {
        "text": _redact_witness_sha(_capture(_dirty_repo(), ["save", "-m", "add new_file"])),
        "json": _redact_witness_sha(_capture(_dirty_repo(), ["save", "-m", "add new_file", "--json"])),
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


_WITNESS_SHA_TEXT_RE = re.compile(r"(save |undo )[0-9a-f]{7}(?=[\s\n:]|$)")


def _redact_witness_sha(capture: dict) -> dict:
    """`save`/`undo` are the only verbs that print their own witness commit's sha (every other
    verb reports only content-addressed op ids). That commit is freshly made with the real
    wall-clock author/committer date (no pinned timestamp, unlike the corpus fixtures), so its sha
    is not reproducible across runs -- freeze a placeholder instead of the literal value."""
    out = capture["out"]
    if out.startswith("{"):
        sha = json.loads(out).get("commit")
        if sha:
            out = out.replace(sha, "<witness-sha>")
    else:
        out = _WITNESS_SHA_TEXT_RE.sub(r"\1<witness-sha>", out)
    return {**capture, "out": out}


_BOOKKEEPING_SHA_RE = re.compile(r'([("]|"save": ")[0-9a-f]{7}(" ?,?\s*(?:"subject": )?"ugh |\s"ugh )')


def _redact_bookkeeping_sha(capture: dict) -> dict:
    """A restore names the save that took the step out: ugh's own commit, made with the real
    wall-clock date, so its sha is not reproducible across runs."""
    return {**capture, "out": _BOOKKEEPING_SHA_RE.sub(r"\1<witness-sha>\2", capture["out"])}


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
