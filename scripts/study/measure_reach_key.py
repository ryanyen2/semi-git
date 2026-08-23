#!/usr/bin/env python3
"""Measure the reach key for the study's Foresee trials.

A trial names one piece of work and asks: which of these behaviours run through
the code it added? The answer has to be measured rather than decided by whoever
designed the trial, so this script derives it from the fixture and prints it as
the JSON that goes into docs/study/answer-key.json.

Reach is a call-graph question, so it is answered with a call graph. Behaviours
are CLI commands -- the things a person actually does with the app -- and a
behaviour reaches the work when its command handler transitively calls any of
the work's functions.

Two oracles were tried before this one and both were wrong:

  `git revert` on each piece of work. The waitlist chain conflicts on the first
  revert. That entanglement is the thing the study is about, so the tool that
  fails on it is not the tool to measure with.

  Removing the work's entry points and recording which tests stop passing. This
  fails in both directions. It said the agenda export depends on the slot
  comparison, which it does not -- test_export's fixture registers an attendee,
  so the failure is the fixture's, not the behaviour's. And dropping
  fixture-level failures to fix that lost `cancel` from the waitlist's reach,
  which is genuine: the cascade lives in cli.cmd_cancel. No rule over test
  outcomes separates "the fixture happens to use it" from "the behaviour is
  about it", because the test suite does not know the difference.

The suite is still run, as a cross-check rather than as the oracle: it must be
green at HEAD, and every test that fails when the work is removed must belong to
a command in the measured reach set. A disagreement means the key is wrong.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from pathlib import Path

# Behaviour id -> the command handler that runs it, per project. The wording the
# participant sees lives in web/src/study/tasks.ts and can change without
# orphaning a key; these ids cannot.
#
# The two fixtures are isomorphic but not identically named -- coursecraft
# enrolls students in sections and exports a timetable, confplan registers
# attendees for sessions and exports an agenda -- so each project carries its own
# map and the measured reach sets are compared at the end. A difference means the
# fixtures have drifted apart, which would break the study's isomorphism claim.
HANDLERS: dict[str, dict[str, str]] = {
    "confplan": {
        "register": "cmd_register",
        "cancel": "cmd_cancel",
        "queue": "cmd_waitlist_join",
        "showQueue": "cmd_waitlist_show",
        "promote": "cmd_waitlist_promote",
        "notices": "cmd_notices",
        "search": "cmd_search",
        "agenda": "cmd_agenda",
        "rooms": "cmd_room_audit",
        "stats": "cmd_stats",
        "speaker": "cmd_speaker",
        "scheduleSession": "cmd_session_add",
    },
    "coursecraft": {
        "register": "cmd_enroll",
        "cancel": "cmd_drop",
        "queue": "cmd_waitlist_join",
        "showQueue": "cmd_waitlist_show",
        "promote": "cmd_waitlist_promote",
        "notices": "cmd_notices",
        "search": "cmd_search",
        "agenda": "cmd_export",
        "rooms": "cmd_room_audit",
        "stats": "cmd_stats",
        "speaker": "cmd_instructor",
        "scheduleSession": "cmd_section_add",
    },
}

# The pieces of work each trial names, as the functions they introduced.
TARGETS: dict[str, dict[str, dict[str, list[str]]]] = {
    # E15. The agenda/timetable and CSV export. Named first because it reads
    # sessions, registrations, rooms and slots, so it looks like it reaches
    # broadly; the measured answer is that nothing else goes through it.
    "agenda_export": {
        "confplan": {"export": ["export_markdown", "export_csv"]},
        "coursecraft": {"export": ["export_markdown", "export_csv"]},
    },
    # E17. `ranges_clash` is the normalized comparison it introduced and
    # `room_clashes` is the audit built on it. Named second because it looks like
    # a one-line helper and the measured answer is broad -- `cancel` is two hops
    # out, since cancelling promotes, promotion registers, registering compares.
    "slot_normalization": {
        "confplan": {"slots": ["ranges_clash"], "scheduling": ["room_clashes"]},
        "coursecraft": {"slots": ["ranges_clash"], "scheduling": ["room_clashes"]},
    },
    # E11/E12/E21. Not a trial target -- request 2 removes the waitlist, so
    # predicting its reach first would teach the answer to the block that
    # measures finding it. Kept because the facilitator sheet quotes it.
    "waitlist": {
        "confplan": {
            "registration": ["join_waitlist", "waitlist_for"],
            "promotion": ["promote_next"],
            "notify": ["pending_notices", "clear_notices"],
        },
        "coursecraft": {
            "enrollment": ["join_waitlist", "waitlist_for"],
            "promotion": ["promote_next"],
            "notify": ["pending_notices", "clear_notices"],
        },
    },
}

# `slot_normalization` alone. `agenda_export` was the other trial until the task
# block became locate-and-reverse; the prediction now rides on the step that
# reverts, and there is exactly one of those. The target is still measured and
# still printed, because the facilitator sheet quotes its reach and because a
# second measured target is the only cross-check that this script's call graph
# agrees with anything.
TRIALS = ["slot_normalization"]

STUB = ('\n\n# --- reach cross-check: this piece of work removed ---\n'
        'def {name}(*a, **k):\n'
        '    raise RuntimeError("removed for reach measurement")\n')


def call_graph(pkg: Path) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """(module, function) -> the (module, function) pairs its body calls.

    Resolves `mod.fn()` against the module's imports and bare `fn()` against the
    module itself. Anything it cannot resolve is dropped, which can only make
    reach look smaller -- so a behaviour in the measured set is there because a
    real edge put it there.
    """
    modules = {p.stem: p for p in pkg.glob("*.py") if p.stem != "__init__"}
    graph: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for mod, path in modules.items():
        tree = ast.parse(path.read_text())
        # Local alias -> module name, for both `import x as y` and `from . import x`.
        alias: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    tail = a.name.rsplit(".", 1)[-1]
                    if tail in modules:
                        alias[a.asname or tail] = tail
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name in modules:
                        alias[a.asname or a.name] = a.name
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                    if target := alias.get(f.value.id):
                        graph[(mod, fn.name)].add((target, f.attr))
                elif isinstance(f, ast.Name):
                    graph[(mod, fn.name)].add((mod, f.id))
    return graph


def closure(graph, root: tuple[str, str]) -> set[tuple[str, str]]:
    seen, queue = {root}, deque([root])
    while queue:
        for nxt in graph.get(queue.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def run_suite(root: Path, python: str) -> tuple[set[str], set[str], int]:
    proc = subprocess.run(
        [python, "-m", "pytest", "-q", "--tb=no", "-ra"],
        cwd=root, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    # `-ra`, not `-rf`: a test whose FIXTURE calls the removed function is
    # reported as an error, and `-rf` leaves errors out of the short summary.
    #
    # The two are kept apart because they mean different things. FAILED is the
    # test's own body reaching the code, so it has to agree with the call graph
    # or one of the two is wrong. ERROR is the fixture reaching it, which says
    # nothing about the behaviour under test -- test_export's fixture registers
    # an attendee, so every export test errors when the slot comparison goes,
    # while the agenda command never calls it.
    body = set(re.findall(r"^FAILED (tests/\S+?)(?:\s|$)", out, re.M))
    setup = set(re.findall(r"^ERROR (tests/\S+?)(?:\s|$)", out, re.M))
    total = sum(int(n) for n in re.findall(r"(\d+) (?:passed|failed|error)", out))
    return body, setup, total


# Which command each test file exercises, for the cross-check only. A test file
# with no entry makes the check fail loudly rather than pass quietly, so adding a
# test file to a fixture cannot silently shrink the cross-check's coverage.
TEST_COMMANDS: dict[str, list[str]] = {
    "test_register.py": ["register"],
    "test_enroll.py": ["register"],
    "test_capacity.py": ["register"],
    "test_clashes.py": ["register"],
    "test_conflicts.py": ["register"],
    "test_series.py": ["register"],
    "test_prereqs.py": ["register"],
    "test_cancel.py": ["cancel"],
    "test_drop.py": ["cancel"],
    "test_waitlist.py": ["queue", "showQueue"],
    "test_promotion.py": ["promote", "cancel"],
    "test_notify.py": ["notices", "promote"],
    "test_search.py": ["search"],
    "test_export.py": ["agenda"],
    "test_rooms.py": ["rooms"],
    "test_stats.py": ["stats"],
    "test_speaker.py": ["speaker"],
    "test_instructor.py": ["speaker"],
    # Unit tests of helpers, below the command layer. Excluded rather than
    # unmapped: they exercise no command, so they can neither confirm nor
    # contradict a reach set.
    "test_slots.py": [],
    "test_storage.py": [],
    "test_catalog.py": [],
}


def measure_project(src: Path, python: str) -> tuple[dict, dict, int]:
    """The reach key for one fixture, plus its fixture-only failures."""
    name = src.name
    pkg = src / name
    handlers = HANDLERS[name]
    graph = call_graph(pkg)

    missing = [b for b, h in handlers.items() if ("cli", h) not in graph]
    if missing:
        sys.exit(f"{name}: the key is stale -- no handler in cli.py for {missing}")

    reaches = {b: closure(graph, ("cli", h)) for b, h in handlers.items()}
    key: dict[str, dict] = {}
    for target, per_project in TARGETS.items():
        spec = per_project[name]
        fns = {(mod, fn) for mod, names in spec.items() for fn in names}
        gone = [f"{m}.{f}" for m, f in sorted(fns)
                if not re.search(rf"^def {re.escape(f)}\b", (pkg / f"{m}.py").read_text(), re.M)]
        if gone:
            sys.exit(f"{name}: the key is stale -- {target} names functions that are gone: {gone}")
        hit = sorted(b for b, seen in reaches.items() if seen & fns)
        key[target] = {"reach": hit,
                       "notReached": sorted(b for b in handlers if b not in hit)}

    body, setup, total = run_suite(src, python)
    if body or setup:
        sys.exit(f"{name}: the suite is not green at HEAD, so the cross-check "
                 f"means nothing: {sorted(body | setup)}")

    problems: list[str] = []
    via_fixture: dict[str, list[str]] = {}
    for target in TRIALS:
        spec = TARGETS[target][name]
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / name
            shutil.copytree(src, work, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
            for mod, names in spec.items():
                f = work / name / f"{mod}.py"
                f.write_text(f.read_text() + "".join(STUB.format(name=n) for n in names))
            body, setup, _ = run_suite(work, python)
        reach = set(key[target]["reach"])
        if setup:
            via_fixture[target] = sorted(setup)
        for node in sorted(body):
            fname = node.split("::")[0].split("/")[-1]
            commands = TEST_COMMANDS.get(fname)
            if commands is None:
                problems.append(f"{name}/{target}: {node} exercises no command this check knows")
            elif commands and not (set(commands) & reach):
                problems.append(f"{name}/{target}: {node} breaks, but "
                                f"{'/'.join(commands)} is not in the reach set")
    if problems:
        key["CROSSCHECK_PROBLEMS"] = problems  # type: ignore[assignment]
    return key, via_fixture, total


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "~/repos/sgt-study").expanduser()
    keys, fixtures, totals, problems = {}, {}, {}, []
    for name in sorted(HANDLERS):
        src = root / name
        if not (src / name).is_dir():
            sys.exit(f"no package at {src / name}")
        venv = src / ".venv/bin/python"
        python = str(venv) if venv.exists() else sys.executable
        key, via_fixture, total = measure_project(src, python)
        problems += key.pop("CROSSCHECK_PROBLEMS", [])  # type: ignore[arg-type]
        keys[name], fixtures[name], totals[name] = key, via_fixture, total

    # The study claims the two fixtures are isomorphic, and the reach key is the
    # sharpest place that claim can fail: if the same named work reaches
    # different behaviours in the two projects, participants in the two projects
    # are not answering the same question, and the counterbalancing is void.
    a, b = sorted(HANDLERS)
    for target in TRIALS:
        if keys[a][target]["reach"] != keys[b][target]["reach"]:
            problems.append(
                f"{target}: the fixtures are not isomorphic -- "
                f"{a} reaches {keys[a][target]['reach']}, {b} reaches {keys[b][target]['reach']}")

    out = {
        "version": "reach-key-v1",
        "behaviours": list(HANDLERS[a]),
        "trials": TRIALS,
        "totalTests": totals,
        "targets": {t: keys[a][t]["reach"] for t in TARGETS},
        "notReached": {t: keys[a][t]["notReached"] for t in TARGETS},
        "perProject": keys,
        # Recorded, not scored. A maintainer taking the work out does have to
        # touch these fixtures, so the paper can cite them; they are not reach.
        "touchedOnlyByFixtures": fixtures,
    }
    if problems:
        out["CROSSCHECK_PROBLEMS"] = problems
        print(json.dumps(out, indent=2))
        return 1

    # Written into the one key file the experimenter loads, rather than left as a
    # second artifact to remember. Only the generated fields are touched, and the
    # run is idempotent, so regenerating after a fixture change is safe.
    key_path = Path(__file__).resolve().parents[2] / "docs/study/answer-key.json"
    if key_path.exists() and "--print" not in sys.argv:
        doc = json.loads(key_path.read_text())
        for trial, request in zip(TRIALS, ("d3",)):
            entry = doc["requestKeys"].setdefault(request, {})
            entry["reach"] = out["targets"][trial]
            entry["_reachNote"] = (
                f"GENERATED by scripts/study/measure_reach_key.py -- do not edit by hand. "
                f"Target {trial}. Behaviour ids the work reaches, measured as call-graph "
                f"reach from each command handler and cross-checked against the suite in "
                f"both fixtures. Not reached: {', '.join(out['notReached'][trial])}.")
        doc["reachKeyVersion"] = out["version"]
        key_path.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"wrote reach for {', '.join(TRIALS)} into {key_path}", file=sys.stderr)

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
