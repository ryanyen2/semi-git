"""WP-V3: run sgt over repos nobody here built, and report what happens.

Question (plan WP-V3): does the machinery work outside author repos at all?

Per repo, in order: clone, pin HEAD, `sgt init` under a 30-minute cap, **drive the genesis backfill to
completion**, `sgt log --summary`, `sgt advanced fsck` (both modes), a reconstruction check, and one
scripted edit + `sgt save`. Everything lands as JSON under `docs/eval/v3-corpus/<owner>__<repo>/run.json`.

*The backfill step is not optional and is the reason this file exists in its current form.* `sgt init`
mines one 10-second chunk walking backward from HEAD (`sgt/core/lens.py:59,709`); the walk to genesis
continues only on subsequent `get()` calls, and no CLI verb drives it. Measured without that step,
pudo/dataset (746 commits) reported reconstruction 0.0 with `dataset/util.py` composing to 35 bytes
against 6421 on disk -- because sgt had seen ~10 seconds of its history, not because composition is
broken. Worse, every sgt invocation advances the walk, so the same untouched repo reported
`9 files / 155 symbols` and then `16 files / 193 symbols` two commands apart: metrics taken before
`reached_genesis` are not reproducible even against themselves. A repo whose walk hits the cap is
reported as `backfill_capped` and its metrics are never computed.

    python -u docs/eval/v3-corpus/harness.py --n 30 --work /tmp/v3
    python -u docs/eval/v3-corpus/harness.py --only pudo/dataset          # one repo, for the referee
    python -u docs/eval/v3-corpus/harness.py --selftest                   # metric controls, no clone

Three things about the metrics, stated here because each is a place the number could flatter us.

*Reconstruction.* `sgt advanced fsck --tree` is sgt's own comparison of `code(current_ideal)` against
the HEAD tree, so a drifted path is a path whose recorded ops do **not** regenerate its bytes. The
reconstruction rate is `1 - drifted/tracked`, computed from that list rather than from anything this
harness composes itself -- if sgt's composition is wrong, the number should show it, and a
reimplementation here could hide it.

*Coverage.* The plan asks for a symbol-level distribution, and `sgt log --summary`'s
`coverage_fraction` is **not** one: at `sgt/api.py:190` it is `len(entity_paths) / len(covered)`, a
fraction of *paths* with at least one live entity. A repo of 100 files where every file is tracked
whole-file-only and one file has a single function scores 1% on that metric whether it holds ten
symbols or ten thousand. Both are recorded: `coverage_fraction_paths` verbatim from sgt (so the
paper's own printed figure is auditable) and `symbol_kinds`, counted over the live ideal's footprints
using `sgt.core.op._symbol_kind` -- sgt's own classifier, not a copy of it.

*Init failure.* A clone that fails, a timeout, and a crash are three different findings and are
recorded as three different `status` values. Collapsing them into "init failure rate" would let a
network flake read as a defect in sgt, or a crash hide inside a cap-out.

The plan's stop-and-ask: >30% init failure across the sweep means stop and fix before continuing.
This script prints that check but does not enforce it -- a human decides.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backfill  # noqa: E402  -- sibling module, same directory

HERE = Path(__file__).parent
SELECTION = HERE / "selection.json"
INIT_CAP_S = 30 * 60
CLONE_CAP_S = 10 * 60
STEP_CAP_S = 10 * 60
BACKFILL_CAP_S = 30 * 60  # per repo, on top of init; a capped walk is reported, never measured


def run(cmd: list[str], cwd: Path | None = None, cap: int = STEP_CAP_S) -> dict:
    """A recorded subprocess. A timeout is a result, not an exception."""
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=cap)
        rc, out, err, timed_out = p.returncode, p.stdout, p.stderr, False
    except subprocess.TimeoutExpired as e:
        rc, out, err, timed_out = None, (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout", True
    return {"cmd": " ".join(cmd[:4]), "rc": rc, "seconds": round(time.monotonic() - t0, 1),
            "timed_out": timed_out, "out": out[-2000:], "err": err[-2000:]}


def run_json(cmd: list[str], cwd: Path, cap: int = STEP_CAP_S) -> dict | list:
    """Run a `--json` command and parse its *full* stdout.

    Must not go through `run()`: that truncates stdout to the last 2000 chars for the record, which
    silently decapitates any JSON payload larger than that. It read as `"fsck": null` on the first
    corpus repo and as a failed `--summary` parse on the second -- both looked like sgt defects and
    were mine. Parsing happens on the untruncated text; only the failure diagnostic is truncated.
    """
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=cap)
    except subprocess.TimeoutExpired:
        return {"_parse_failed": "timeout", "_rc": None, "_seconds": cap}
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as e:
        return {"_parse_failed": str(e), "_rc": p.returncode,
                "_seconds": round(time.monotonic() - t0, 1),
                "_out_len": len(p.stdout),
                "_raw_head": p.stdout[:600], "_raw_tail": p.stdout[-600:],
                "_raw_err": p.stderr[-600:]}


def payload(v: dict | list | None) -> dict:
    """A parsed JSON object, or `{}` when parsing failed -- so a parse failure reads as absent data
    rather than silently becoming a zero in a metric."""
    return v if isinstance(v, dict) and "_parse_failed" not in v else {}


def tree_facts(repo: Path) -> dict:
    """Size of the thing sgt was pointed at, so a bad number can be read against the repo's shape."""
    files = [p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    py = [p for p in files if p.suffix == ".py"]
    commits = run(["git", "rev-list", "--count", "HEAD"], repo)
    return {
        "files_total": len(files),
        "files_py": len(py),
        "py_bytes": sum(p.stat().st_size for p in py),
        "commits": int(commits["out"].strip()) if (commits["out"] or "").strip().isdigit() else None,
    }


SYMBOL_KINDS_SNIPPET = """
import json
from sgt.core.op import _symbol_kind
from sgt.core.lens import get, current_ideal
from sgt.core.store import Store
get('.')
live = set(current_ideal('.').op_ids)
# Distinct symbols, not footprint occurrences: a function edited in 30 ops is one symbol. Counting
# occurrences would let a hot file inflate the entity count and read as better coverage.
by_kind, kinds_of_path = {}, {}
for op in Store('.').all_ops():
    if op.id not in live:
        continue
    for sym in op.footprint:
        k = _symbol_kind(sym)
        by_kind.setdefault(k, set()).add(sym)
        kinds_of_path.setdefault(sym.split('::', 1)[0], set()).add(k)
inside = {p for p, ks in kinds_of_path.items() if ks & {'entity', 'nested'}}
print(json.dumps({
    'symbol_kinds': {k: len(v) for k, v in by_kind.items()},
    'paths': len(kinds_of_path),
    # The question the coverage number is really asking: did sgt parse into the file, or is the
    # file's whole history one opaque blob?
    'paths_with_entities': len(inside),
    'paths_whole_file_only': len(kinds_of_path) - len(inside),
    'live_ops': len(live),
    # Set only after the scripted edit: did `sgt save` actually put the new symbol in the ideal?
    'has_probe': any(s.endswith('::sgt_v3_probe') for v in by_kind.values() for s in v),
    # Paths sgt parsed into, so the scripted edit can target one it actually covers rather than the
    # biggest file on disk (which on the first corpus repo was untracked, so `sgt save` correctly had
    # nothing to record and the probe measured nothing).
    'entity_paths': sorted(p for p in inside if p.endswith('.py')),
}))
"""


def symbol_kinds(repo: Path) -> dict:
    """The symbol-level distribution the plan asks for, via sgt's own classifier.

    Goes through `run_json`, not `run`: `entity_paths` is a list of every covered .py path, which
    passes 2000 chars on any real codebase and would be truncated into a parse failure."""
    data = run_json([sys.executable, "-c", SYMBOL_KINDS_SNIPPET], repo)
    return {"ok": "_parse_failed" not in data, "data": payload(data),
            "failure": data if "_parse_failed" in data else None}


def scripted_edit(repo: Path, covered: list[str]) -> dict:
    """Append one function to a .py file sgt covers, and save it. The plan asks for 'one scripted
    edit' -- appending a new top-level def is the least ambiguous one available: it needs no parse of
    existing code, it is valid in any Python file, and it is exactly the shape sgt claims to record at
    entity granularity, so a failure here is a failure at the thing being measured.

    The target comes from sgt's own set of entity-covered paths, not from file size. Picking the
    largest `.py` file put the probe on `dataset/table.py`, which sgt does not track -- `sgt save`
    then correctly reported nothing to save and the probe measured nothing at all.

    `sgt save` returning 0 is not evidence it recorded anything -- a command that succeeds while doing
    nothing is this system's characteristic failure. So the edit is checked three ways: the new symbol
    appears in the live ideal, the tree still reconstructs, and the file is left edited (not reverted,
    which would manufacture drift a later reader would misread as a defect)."""
    cands = sorted((repo / p for p in covered if (repo / p).is_file()),
                   key=lambda p: p.stat().st_size, reverse=True)
    if not cands:
        return {"skipped": "sgt covers no .py path at entity granularity"}
    target = cands[0]
    original = target.read_bytes()
    target.write_bytes(original.rstrip(b"\n") + b"\n\n\ndef sgt_v3_probe():\n    return 1\n")

    step = run(["sgt", "save", "-m", "v3 probe edit"], repo)
    after = symbol_kinds(repo)
    fsck_after = payload(run_json(["sgt", "advanced", "fsck", "--tree", "--json"], repo))
    return {
        "path": str(target.relative_to(repo)),
        "save": step,
        "recorded_symbol": bool((after.get("data") or {}).get("has_probe")),
        "drift_after": len(fsck_after.get("drift") or []),
        "drift_paths_after": (fsck_after.get("drift") or [])[:20],
        "symbols_after": after.get("data"),
    }


def run_repo(spec: dict, work: Path, out_dir: Path) -> dict:
    """Drive one repo and write its record, whatever the outcome.

    The record is written here rather than at the end of `_run_repo` because four of that function's
    five exits are early ones: a capped backfill, a crashed init, a failed clone all returned without
    writing anything, so the only trace of them was a line of sweep stdout. Two repos of the first
    30-repo sweep were lost that way -- their timings, their load average, their clone facts -- and a
    sweep whose failures leave no record cannot be audited or re-run selectively."""
    rec = _run_repo(spec, work, out_dir)
    d = out_dir / spec["full_name"].replace("/", "__")
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps(rec, indent=2) + "\n")
    return rec


def _run_repo(spec: dict, work: Path, out_dir: Path) -> dict:
    name = spec["full_name"].replace("/", "__")
    dest = work / name
    rec: dict = {"repo": spec["full_name"], "stars": spec.get("stars"),
                 "band": spec.get("band"), "license": spec.get("license"), "status": None}

    if dest.exists():
        shutil.rmtree(dest)
    clone = run(["git", "clone", "--quiet", spec["clone_url"], str(dest)], cap=CLONE_CAP_S)
    rec["clone"] = clone
    if clone["rc"] != 0:
        rec["status"] = "clone_timeout" if clone["timed_out"] else "clone_failed"
        return rec

    head = run(["git", "rev-parse", "HEAD"], dest)
    rec["head_sha"] = (head["out"] or "").strip() or None
    rec["tree"] = tree_facts(dest)

    # The init cap is a wall-clock cap, so a busy machine can manufacture a timeout that reads as an
    # sgt defect. The load at start is recorded with every init; any `init_timeout` measured under
    # load must be re-run on an idle machine before it counts as a finding.
    rec["loadavg_at_init"] = os.getloadavg()[0]
    init = run(["sgt", "init"], dest, cap=INIT_CAP_S)
    rec["init"] = init
    if init["timed_out"]:
        rec["status"] = "init_timeout"
        return rec
    if init["rc"] != 0:
        rec["status"] = "init_crashed"
        return rec

    # `sgt init` mines ONE 10-second chunk backward from HEAD; the walk to genesis continues only on
    # later `get()` calls, and no CLI verb drives it (see backfill.py). Measuring reconstruction or
    # coverage before `reached_genesis` measures a half-built artifact -- and the numbers move on every
    # sgt invocation, so they are not even reproducible. Drive it first, and refuse to report metrics
    # from a capped walk.
    rec["backfill"] = backfill.drive(dest, BACKFILL_CAP_S, quiet=True)
    if not rec["backfill"]["reached_genesis"]:
        rec["status"] = "backfill_capped"
        return rec

    rec["summary"] = run_json(["sgt", "log", "--summary", "--json"], dest)
    rec["fsck"] = run_json(["sgt", "advanced", "fsck", "--json"], dest)
    rec["fsck_tree"] = run_json(["sgt", "advanced", "fsck", "--tree", "--json"], dest)
    rec["symbols"] = symbol_kinds(dest)

    summary, fsck_tree = payload(rec["summary"]), payload(rec["fsck_tree"])
    tracked = summary.get("files")
    # An absent `drift` key means the fsck payload is missing, NOT that nothing drifted. `len(None or
    # [])` is 0, and that is how a parse failure printed itself as `0 drifted files` -- perfect
    # reconstruction -- on the first corpus repo. `payload()` guards the dict; this guards the
    # arithmetic one layer below it, which is where the fabricated zero actually came from.
    drift_list = fsck_tree.get("drift")
    drifted = len(drift_list) if drift_list is not None else None
    # This rate's denominator is `summary["files"]` -- the count of paths sgt *claims*, not the repo's
    # file count -- so it both omits files sgt never recorded and charges it for paths that no longer
    # exist. `recompute.py` derives the honest rate from the `fsck_tree` lists below; it is the number
    # to report. This one is kept only because the sweep was pre-registered against it.
    rec["reconstruction"] = {
        "tracked_files": tracked,
        "drifted_files": drifted,
        "rate": (round(1 - drifted / tracked, 4)
                 if tracked and drifted is not None else None),
        "drifted_paths": (drift_list or [])[:20],
    }
    rec["coverage_fraction_paths"] = summary.get("coverage_fraction")

    rec["edit"] = scripted_edit(dest, ((rec["symbols"].get("data") or {}).get("entity_paths") or []))
    rec["status"] = "ok"
    return rec


def _selftest() -> int:
    """Controls for the two derived metrics. Neither needs a clone."""
    bad = []
    from sgt.core.op import _symbol_kind
    for sym, want in [
        ("a.py::helper", "entity"),
        ("a.py::__residue__::helper", "residue"),
        ("a.py::__anchor__::helper", "anchor"),
        ("a.py", "whole_file"),
    ]:
        got = _symbol_kind(sym)
        if got != want:
            bad.append(f"_symbol_kind({sym!r}) = {got!r}, want {want!r}")

    # The reconstruction arithmetic, including the shape that must not read as success: 0 tracked
    # files is "sgt recorded nothing", and 1.0 would be the most flattering possible answer to it.
    # (10, None) is the case that mattered: a missing fsck payload must yield no rate at all, never
    # the flattering 1.0 that `len(None or [])` produced.
    for tracked, drifted, want in [(10, 0, 1.0), (10, 5, 0.5), (4, 4, 0.0), (0, 0, None),
                                   (10, None, None), (None, 0, None)]:
        got = (round(1 - drifted / tracked, 4)
               if tracked and drifted is not None else None)
        if got != want:
            bad.append(f"reconstruction({tracked}, {drifted}) = {got}, want {want}")

    # A parse failure must read as absent data, never as a zero in a metric. Recording bare `None`
    # for `fsck --json` is how a whole repo's result told me nothing about why it was missing.
    checks = 10  # 4 _symbol_kind cases + 6 reconstruction cases above
    for v, want in [
        ({"drift": ["a"]}, {"drift": ["a"]}),
        ({"_parse_failed": "boom", "_raw_out": ""}, {}),
        (None, {}),
        ([1, 2], {}),
    ]:
        checks += 1
        if payload(v) != want:
            bad.append(f"payload({v!r}) = {payload(v)!r}, want {want!r}")

    # The gate that today's error needed: a capped walk must never reach the metric code at all.
    checks += 1
    src = Path(__file__).read_text()
    gate = 'if not rec["backfill"]["reached_genesis"]:'
    # Marker built by concatenation so this literal does not match *itself* in the source and make the
    # check vacuous -- which is what it did after the run_json rename, while still reporting green.
    marker = 'rec["summary"] = ' + "run_json"
    if gate not in src or marker not in src or src.index(gate) > src.index(marker):
        bad.append("run_repo must return before computing metrics when the backfill is capped")

    for line in bad:
        print("FAIL " + line)
    print(f"selftest: {checks - len(bad)}/{checks}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="repos to complete (plan: 30)")
    ap.add_argument("--work", default="/tmp/v3", help="where clones go")
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--only", default=None, help="one full_name, for a referee re-run")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    sel = json.loads(SELECTION.read_text())
    cands = sel["candidates"]
    if args.only:
        cands = [c for c in cands if c["full_name"] == args.only]
        if not cands:
            print(f"{args.only} is not in selection.json", file=sys.stderr)
            return 2

    work, out_dir = Path(args.work), Path(args.out)
    work.mkdir(parents=True, exist_ok=True)

    done, skips = [], []
    for spec in cands:
        if len(done) >= args.n:
            break
        rec = run_repo(spec, work, out_dir)
        if rec["status"] == "ok":
            done.append(rec)
        else:
            skips.append({"repo": rec["repo"], "status": rec["status"]})
        # Classify first, then print. `len(done) + 1` numbered the *next* completion, so every skipped
        # repo borrowed the number of the ok repo after it and two lines read `18/30` -- which is how a
        # sweep of 20 attempts looked like a sweep of 18.
        line = (f"{len(done):3d}/{args.n} {rec['status']:14s} {rec['repo']}"
                f" init={rec.get('init', {}).get('seconds')}s")
        if rec["status"] == "ok":
            r = rec["reconstruction"]
            line += f" recon={r['rate']} ({r['drifted_files']}/{r['tracked_files']} drifted)"
        print(line, flush=True)

    # Every skip is logged, per the protocol -- a corpus of 30 that silently dropped 40 candidates is
    # a different corpus than one that dropped 2.
    (out_dir / "sweep.json").write_text(json.dumps({
        "completed": len(done), "skipped": skips,
        "init_failure_fraction": round(len(skips) / max(1, len(skips) + len(done)), 4),
        "reconstruction_rates": [r["reconstruction"]["rate"] for r in done],
    }, indent=2) + "\n")
    print(f"\n{len(done)} completed, {len(skips)} skipped")
    if skips and len(skips) / (len(skips) + len(done)) > 0.30:
        print("STOP-AND-ASK: >30% of attempted repos failed (plan WP-V3). A human decides.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
