"""What the project's check observed, recorded at save (`ugh.core.observe`): its exit code, its
output and, when it ran Python, the lines of the repository's files it executed. One behaviour per
test, each on a small project whose check imports it and prints."""

from __future__ import annotations

import json
import os
import shlex
import sys

import pytest

from ugh.cli import main
from ugh.cli.porcelain import save
from ugh.core import planner
from ugh.store.gitbind import GitBinding, init_store

PYTHON = shlex.quote(sys.executable)
CHECK = f"{PYTHON} check.py"
lines_need_312 = pytest.mark.skipif(sys.version_info < (3, 12), reason="line events need sys.monitoring")

HELLO = 'def main():\n    return "hello"\n'
HELLO_WORLD = 'def main():\n    return "hello " + name()\n\n\ndef name():\n    return "world"\n'


def _project(root, check: str | None = CHECK, **files):
    """A project whose first save holds the check config (when `check` is given), a check.py that
    prints `m.main()`, and m.py."""
    init_store(root)
    (root / ".gitignore").write_text("__pycache__/\n")
    if check is not None:
        (root / ".sgt" / "oracle.json").write_text(json.dumps({"tiers": [{"name": "check", "command": check}]}))
    _save(root, {"m.py": HELLO, "check.py": "import m\n\nprint(m.main())\n", **files}, "base")
    return root


def _save(root, files, message):
    for path, text in files.items():
        (root / path).write_text(text)
    out = save(str(root), message=message)
    assert out["ok"] and out["saved"], out
    return out


def _observation(root, sha):
    path = root / ".sgt" / "local" / "observations" / f"{sha}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def _ran_lines(obs, path):
    return {n for a, b in obs["lines"].get(path, ()) for n in range(a, b + 1)}


def _cli(root, argv, capsys):
    capsys.readouterr()
    cwd = os.getcwd()
    os.chdir(root)
    try:
        rc = main(argv)
    finally:
        os.chdir(cwd)
    return rc, capsys.readouterr().out


def _steps(root, sha):
    return set(planner._Ctx(root).of_commit[sha])


@lines_need_312
def test_a_save_records_the_checks_output_and_the_lines_of_the_function_it_ran(tmp_path):
    root = _project(tmp_path / "p")
    sha = GitBinding(root).head()
    obs = _observation(root, sha)
    assert obs["command"] == CHECK and obs["exit"] == 0 and obs["output"] == "hello\n"
    assert _ran_lines(obs, "m.py") == {2}  # main's body; its def line runs at import, not at a call


@lines_need_312
def test_a_function_the_check_never_calls_is_reported_never_run(tmp_path, capsys):
    root = _project(tmp_path / "p")
    x = _save(root, {"m.py": HELLO + "\n\ndef unused():\n    return 1\n"}, "add unused")["commit"]
    view = planner.to_json(planner.plan(root, _steps(root, x), target=x[:7]))
    assert [(row["address"], row["ran"]) for row in view["undone"]] == [(f"m.py::unused@{x[:7]}", False)]
    _, out = _cli(root, ["revert", "--emit", x[:7]], capsys)
    assert f"the check never ran: m.py::unused@{x[:7]}" in out


@lines_need_312
def test_a_take_out_and_its_bring_back_each_reverse_the_output_delta(tmp_path, capsys):
    root = _project(tmp_path / "p")
    x = _save(root, {"m.py": HELLO_WORLD}, "say hello world")["commit"]
    _, out = _cli(root, ["revert", "--json", "--yes", x[:7]], capsys)
    view = json.loads(out)
    assert view["applied"] and all(row["ran"] for row in view["undone"])
    assert view["observed"]["delta"] == {"added": 1, "removed": 1} and view["observed"]["reverses"] is True
    assert _observation(root, GitBinding(root).head())["output"] == "hello\n"

    _, out = _cli(root, ["restore", "--json", "--yes", x[:7]], capsys)
    view = json.loads(out)
    assert view["applied"] and view["observed"]["delta"] == {"added": 1, "removed": 1}
    assert view["observed"]["reverses"] is True
    assert (root / "m.py").read_text() == HELLO_WORLD


@lines_need_312
def test_taking_out_code_the_check_never_ran_is_not_called_gone(tmp_path, capsys):
    root = _project(tmp_path / "p")
    x = _save(root, {"m.py": HELLO + "\n\ndef unused():\n    return 1\n"}, "add unused")["commit"]
    _, out = _cli(root, ["revert", "--yes", x[:7]], capsys)
    assert "output: 0 line(s) added, 0 removed — those saves never changed it, so the check cannot confirm anything" in out
    assert "as the check sees it" not in out
    assert f"gone from the code; the check cannot confirm its behaviour: m.py::unused@{x[:7]}" in out


@lines_need_312
def test_save_prints_the_verdict_the_output_delta_and_how_many_steps_ran(tmp_path, capsys):
    root = _project(tmp_path / "p")
    (root / "m.py").write_text(HELLO_WORLD)
    rc, out = _cli(root, ["save", "-m", "say hello world"], capsys)
    assert rc == 0
    assert "check passes; output: 1 line(s) added, 1 removed against the parent; the check ran 2 of 2 step(s)" in out


@lines_need_312
def test_show_gives_each_saves_verdict_and_output_delta(tmp_path, capsys):
    root = _project(tmp_path / "p")
    x = _save(root, {"m.py": HELLO_WORLD}, "say hello world")["commit"]
    _, out = _cli(root, ["show", "--json", x[:7]], capsys)
    (row,) = json.loads(out)["saves"]
    assert row["observed"] == {"verdict": "pass", "lines_recorded": True, "delta": {"added": 1, "removed": 1}}


def test_without_a_check_nothing_is_observed_and_take_outs_work_as_before(tmp_path):
    root = _project(tmp_path / "p", check=None)
    out = _save(root, {"m.py": HELLO_WORLD}, "say hello world")
    assert out["observed"] is None and not (root / ".sgt" / "local" / "observations").exists()
    p = planner.plan(root, _steps(root, out["commit"]), target="x")
    view = planner.to_json(p)
    assert view["observed"] is None and [row["ran"] for row in view["undone"]] == [None, None]
    done = planner.to_json(planner.apply(root, p), applied=True)
    assert done["observed"] is None
    assert (root / "m.py").read_text() == HELLO


def test_a_check_that_runs_no_python_has_output_and_no_lines(tmp_path):
    root = _project(tmp_path / "p", check="sh -c 'echo ok'")
    x = _save(root, {"m.py": HELLO_WORLD}, "say hello world")["commit"]
    obs = _observation(root, x)
    assert obs["output"] == "ok\n" and obs["exit"] == 0 and obs["lines"] is None
    view = planner.to_json(planner.plan(root, _steps(root, x), target="x"))
    assert view["observed"] == {"head": {"verdict": "pass", "lines_recorded": False}}
    assert {row["ran"] for row in view["undone"]} == {None}


@lines_need_312
def test_the_projects_own_sitecustomize_still_loads(tmp_path):
    root = _project(tmp_path / "p", check=f'PYTHONPATH="$PYTHONPATH:." {CHECK}',
                    **{"sitecustomize.py": "print('project sitecustomize')\n"})
    obs = _observation(root, GitBinding(root).head())
    assert obs["output"] == "project sitecustomize\nhello\n"
    assert _ran_lines(obs, "m.py") == {2}


def test_a_save_whose_observation_cannot_be_written_still_commits(tmp_path, capsys):
    root = _project(tmp_path / "p")
    observations = root / ".sgt" / "local" / "observations"
    before = GitBinding(root).head()
    observations.chmod(0o500)
    try:
        (root / "m.py").write_text(HELLO_WORLD)
        rc, out = _cli(root, ["save", "-m", "say hello world"], capsys)
    finally:
        observations.chmod(0o700)
    head = GitBinding(root).head()
    assert rc == 0 and head != before and _observation(root, head) is None
    notes = [line.strip() for line in out.splitlines() if "observation not recorded" in line]
    assert len(notes) == 1 and notes[0].startswith("observation not recorded: ") and "denied" in notes[0]


@lines_need_312
def test_a_take_out_of_saves_that_never_changed_the_output_cannot_say_it_reverses(tmp_path, capsys):
    root = _project(tmp_path / "p")
    x = _save(root, {"m.py": HELLO + "\n\ndef spare():\n    return 1\n"}, "add a spare")["commit"]
    _, out = _cli(root, ["revert", "--json", "--yes", x[:7]], capsys)
    view = json.loads(out)
    assert view["applied"] and view["observed"]["delta"] == {"added": 0, "removed": 0}
    assert view["observed"]["saves_delta"] == {"added": 0, "removed": 0}
    assert view["observed"]["reverses"] is None


def test_running_the_check_leaves_no_bytecode_in_the_tree(tmp_path):
    root = _project(tmp_path / "p")
    (root / ".gitignore").unlink()
    _save(root, {"m.py": HELLO_WORLD}, "say hello world")
    assert not list(root.rglob("__pycache__"))
