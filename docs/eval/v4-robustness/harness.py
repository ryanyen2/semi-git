#!/usr/bin/env python3
"""WP-V4: a seeded operation-sequence harness that looks for ways sgt can lose your work.

    python docs/eval/v4-robustness/harness.py --case linear_history --seed 1 --ops 40
    python docs/eval/v4-robustness/harness.py --repo /path/to/clone --seed 7 --ops 50
    python docs/eval/v4-robustness/harness.py --replay docs/eval/v4-robustness/repro-<...>.json

What it does: builds a pinned repo (a `tests/laws/corpus.py` fixture, or a copy of a real clone),
runs `sgt init`, then applies a seeded sequence of mutating operations, checking every oracle after
every operation. The sequence, and the first oracle failure, are written out as a replayable JSON
file so a bug report is a file rather than a story.

The oracles, and why each one is here rather than being left to the unit tests:

* `fsck` -- sgt's own integrity check (hash, chain gaps, invalid ideals, unreachable witnesses,
  mixed miner versions, stale op index). Cheap, and it is the check a user would be told to run.
* `fsck --tree` -- `code(current_ideal)` against the HEAD tree. This is the get-put law of
  `tests/laws/test_roundtrip.py` asked of a *live, mutated* repo rather than a freshly mined one.
* `ideal ⊆ store` -- an ideal naming an op the store does not hold is a dangling ideal; the fold
  would silently drop it.
* `orphan_layout` -- a dead symbol whose trailing gap is still live, so the fold keeps splicing its
  blank lines. Added late (F97) because it is the one class the three checks above *cannot* see: a
  write verb materializes its own composition, so `fsck --tree` compares two copies of the same wrong
  answer. Report-only; it is wrong bytes, not lost bytes.
* **recoverability** (three checks, and the reason this work package exists). A version-control
  tool is allowed to be wrong about clustering. It is not allowed to lose bytes. So:
  the op store never shrinks; every commit sha this run has seen stays a reachable commit; and the
  bytes a revert removed come back through the documented recovery ladder (`restore <op-id>`, then
  `restore <file::symbol>`). That last one is stated in bytes on purpose: an op id that will not
  re-enter the ideal is only a defect if content is unreachable with it, and twice it was not
  (F37). The plan's hard
  stop-and-ask is on exactly these: a violation stops the run immediately and does not continue
  sampling, because after the first lost byte every later observation is measuring a broken repo.

Deliberate limits, stated rather than hidden:

* Operations are applied through the CLI, not the Python API, because the CLI is what a user and
  an agent actually drive and it is where the argument plumbing bugs live.
* `sgt sync` and the two-clone laws are not exercised here (`--clones` is not implemented); this
  file covers the single-repo verbs. `tests/laws/test_convergence.py` covers sync as unit laws.
* An operation that exits non-zero is recorded as `refused` and is *not* a failure by itself --
  many verbs legitimately refuse (nothing to undo, no second feature to merge). A traceback in
  stderr *is* a failure, separately from the oracles: a stack trace reaching a user is a defect.
* No minimization loop. Replay is deterministic (pinned fixture bytes, pinned commit dates, seeded
  RNG), so the recorded sequence *is* the repro; `--replay` re-runs it and `--replay --prefix N`
  cuts it short, which is enough to bisect by hand.
* **The observation apparatus participates.** `sgt fsck --tree` mines on contact (`_fsck_tree`
  calls `lens.get`), so checking the round-trip oracle after every op advances the ideal. This is
  not avoidable through the CLI and it is not wrong -- every real verb mines on contact too, so the
  sequence the harness drives is a sequence a user could drive. But it means a run is
  op-then-observe interleaved, not op-only, and a bug that requires *no* read between two writes
  cannot be reached from here.
* The law oracles this file asks of a live repo are the round-trip/get-put pair and integrity;
  `test_coverage_every_path_has_an_image` and `test_no_duplicate_entity_ids_per_file` are not
  re-implemented here, and LAW-U/LAW-R are unreachable without sync. Reported as covered by the
  unit suite, not by this sweep.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

TIMEOUT = 300
# Bumped whenever an oracle, a probe, or the recorded artifact shape changes, so that pooling two
# artifacts with different numbers here is a visible error rather than an inference from mtimes.
HARNESS_VERSION = 9


def sgt(repo: Path, *args: str) -> tuple[int, str, str]:
    p = subprocess.run(["sgt", *args], cwd=repo, capture_output=True, text=True, timeout=TIMEOUT)
    return p.returncode, p.stdout, p.stderr


def git(repo: Path, *args: str) -> tuple[int, str, str]:
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=TIMEOUT)
    return p.returncode, p.stdout, p.stderr


def store_ids(repo: Path) -> set[str]:
    """The durable op store: one file per op under `.sgt/ops`."""
    d = repo / ".sgt/ops"
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


def ideal_ids(repo: Path) -> set[str]:
    """The persisted ideal for the checked-out ref. A plain file read -- deliberately *not*
    `lens.get()`, which mines and advances the ideal, i.e. would mutate what it is measuring."""
    f = repo / ".sgt/local/ideal.json"
    if not f.is_file():
        return set()
    table = json.loads(f.read_text()).get("data") or {}
    ref = (repo / ".git/HEAD").read_text().strip().removeprefix("ref: ")
    return set(table.get(ref) or [])


def head_sha(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")[1].strip()


def system_version() -> dict:
    """The version of sgt this run tested. The first pooled table mixed artifacts written across two
    days of active fixing and nothing in the files said so -- the only trace was their mtimes, and two
    of the seven even carried a different key set than the other five. Runs that do not record their
    own system version cannot be pooled honestly, so record it: the commit, plus a digest of whatever
    is uncommitted under `sgt/` (which is where every fix made during an evaluation lands first).

    Also digests *this file*, because `HARNESS_VERSION` is an integer I have to remember to bump and the
    whole point of a version stamp is not relying on my memory. An instrument change is as good a reason
    to refuse a pool as a system change: `orphan_layout` and `target_kind` both arrived without the
    system moving, and both change what an artifact means. Unlike `sgt/`, which is re-invoked as a
    subprocess for every operation, this file is read once at process start -- so editing it cannot make
    a single run version-mixed, only make two runs incomparable."""
    rc, sha, _ = git(ROOT, "rev-parse", "HEAD")
    rc2, diff, _ = git(ROOT, "diff", "HEAD", "--", "sgt")
    rc3, status, _ = git(ROOT, "status", "--porcelain", "--", "sgt")
    dirty = f"{diff}\n{status}" if rc2 == 0 and rc3 == 0 else None
    return {"harness_version": HARNESS_VERSION,
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16],
            "head": sha.strip() if rc == 0 else "unknown",
            "sgt_dirty_sha256": hashlib.sha256(dirty.encode()).hexdigest()[:16] if dirty else "unknown",
            "sgt_dirty_lines": len(diff.splitlines()) if rc2 == 0 else -1}


def features(repo: Path) -> dict[str, dict]:
    rc, out, _ = sgt(repo, "log", "--json")
    if rc != 0:
        return {}
    try:
        return json.loads(out).get("features") or {}
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------- oracles


@dataclass
class Violation:
    oracle: str
    detail: str
    recoverability: bool


def tracked_files(repo: Path) -> list[str]:
    """Tracked regular files. `-z` and a blob-mode filter, never `.split()` (F69).

    `git ls-files` without `-z` separates by newline and C-quotes any path holding non-ASCII bytes, so
    `.split()` shreds a path containing a space into tokens and mangles a quoted one. Here that is worse
    than a bad rate: both callers below feed a *loss* check, and a shredded or quoted name fails the
    `is_file()` guard and drops out silently -- so real data loss in a path with a space or a non-ASCII
    character would go unreported. V4's own fixtures are ASCII and spaceless, so no published V4 number
    moves; the defect was live the moment `--case` pointed at a real clone.
    """
    keep = []
    for entry in git(repo, "ls-files", "-z", "-s")[1].split("\x00"):
        meta, _, path = entry.partition("\t")        # "<mode> <sha> <stage>\t<path>"
        if path and meta.split(" ", 1)[0] in ("100644", "100755"):
            keep.append(path)
    return keep


def tracked_bytes(repo: Path) -> dict[str, bytes]:
    """Every tracked file's bytes. This, not the ideal's op-id set, is what "nothing was lost" means:
    two different op sets can compose the same file (an inverse splice whose effect is already
    superseded contributes nothing), and a round trip that ends with a different op set but identical
    bytes has lost nothing a user can observe."""
    return {p: (repo / p).read_bytes() for p in tracked_files(repo) if (repo / p).is_file()}


def footprint_of(repo: Path, op_id: str) -> list[str]:
    """Every footprint key of an op, read straight off its store file. Layout keys included."""
    f = repo / ".sgt/ops" / op_id
    if not f.is_file():
        return []
    try:
        return list(json.loads(f.read_text()).get("footprint") or {})
    except json.JSONDecodeError:
        return []


def symbols_of(repo: Path, op_id: str) -> list[str]:
    """The *addressable* symbols an op touches -- what you could type after `sgt restore`. Layout keys
    (`file::__residue__::x`, `file::__anchor__::x`) are excluded because the CLI does not accept them.
    Used to try `restore <file::symbol>` when `restore <op-id>` refuses: they are different code paths in
    the CLI's resolution ladder and, as of F37, they do not agree."""
    return [s for s in footprint_of(repo, op_id) if "::__" not in s and "::" in s]


def paths_of(repo: Path, op_id: str) -> set[str]:
    """The files an op writes to. Distinct from `symbols_of` on purpose, and the distinction is a fix
    (calibration error #7, 2026-08-16): 22 of the 31 ops in a fresh `linear_history` have *only* layout
    keys in their footprint, so deriving the byte-comparison scope from `symbols_of` gave an empty scope
    for two thirds of the store -- no file to compare, no drift ever found. F35 is the proof that this
    matters: an orphaned residue op is exactly a one-byte difference in a real file. Restore-by-symbol
    needs the narrow list; deciding which bytes to check needs the wide one."""
    return {s.split("::", 1)[0] for s in footprint_of(repo, op_id) if "::" in s}


def creating_ops(repo: Path) -> frozenset[str]:
    """Ops that *create* a code symbol -- some footprint entry whose `before` is None. This is
    `subtract._born_symbols`, and it is the gate on `broken_references`: `_broken_references` builds
    `removed_names` from the born set and returns `()` immediately when it is empty, so **both** of its
    sweeps are dead for a revert that only rolls a symbol back one version. A random single-op revert
    picks a mid-chain rework almost every time, which is why the warning stayed silent.

    Measured 2026-08-19 on throwaway copies of two V3 clones, `revert <id> --emit --json`:

        pool        broken_references   kept_conflicts
        creators          7/33 (21%)        2/33
        uniform           1/60 (1.7%)       4/60

    Creation ops are 2.7% (stammer) and 5.2% (pudo__dataset) of the live ideal, so uniform draws reach
    them about as often as that and the ~12x difference above is the whole reason the class looked
    unreachable. This replaced a weighting toward *referenced* ops, which fired 0/28 on targeted draws:
    a recorded dependency is what sweep 1 reads, but neither sweep runs at all unless a symbol is
    un-created, and reverting one op of a live chain does not un-create anything.

    Aimed at the wrong thing twice before this: the retraction and both wrong denominators are in the
    ledger for 2026-08-17. The property that gates the guard was in `subtract.py:56-62` the whole
    time."""
    out: set[str] = set()
    for op_id in store_ids(repo):
        try:
            rec = json.loads((repo / ".sgt/ops" / op_id).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for sym, ba in (rec.get("footprint") or {}).items():
            if "::__" in sym or "::" not in sym:
                continue
            if isinstance(ba, (list, tuple)) and ba and ba[0] is None:
                out.add(op_id)
                break
    return frozenset(out)


def resurrection_kind(repo: Path, op_id: str) -> str:
    """`layout` if every symbol this op writes is inter-entity bookkeeping, `content` if any of it is
    user code. Splits `restore_resurrects_excluded`, which conflated the two (R2 deviation #4,
    2026-08-16, pre-registered before the re-run).

    A restore is *obliged* to bring an entity's `__anchor__` and `__residue__` siblings back with it --
    they sit outside the downset, so `plan_restore` pulls them in explicitly, and without them the fold
    has no separator to place and composes `    return 2def revived():` (F35). When a previous revert had
    removed one, it comes back, and the old oracle called that work pulled back. Reading all 34 distinct
    resurrected ids from the frozen baseline: 18 were anchors whose whole image is `\\x00FIRST\\x00`, 14
    were whitespace-only residues, and **2 carried real code** -- a method body, and an
    `if __name__ == "__main__":` block living in the gap after `run` because module-level statements have
    no entity to belong to. So 32/34 was the instrument, inflating the class ~16x, and 2/34 is the actual
    defect: `__residue__` conflates whitespace with module-level user code, so a restore that needs a
    separator can silently return code an earlier revert removed.

    Anchors are metadata by construction (a predecessor's name, or `\\x00FIRST\\x00`) and never judged by
    bytes. Residues are judged by bytes, with the leading-gap sentinel stripped first. Anything else --
    an entity, a whole file, a nested symbol -- is content by definition. An op that cannot be read is
    called content: this decides what gets excused, so the unreadable case must fail loud."""
    f = repo / ".sgt/ops" / op_id
    try:
        payload = json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return "content"
    images = payload.get("images") or {}
    for sym in payload.get("footprint") or {}:
        if "::__anchor__::" in sym:
            continue
        if "::__residue__::" not in sym:
            return "content"
        raw = images.get(sym)
        try:
            img = bytes.fromhex(raw) if isinstance(raw, str) else b""
        except ValueError:
            return "content"
        if img.replace(b"\x00HEAD\x00", b"").strip():
            return "content"
    return "layout"


# How many times the restore ladder may repeat before the probe judges. Each pass that changes anything
# admits at least one op and `removed` is finite, so the loop ends on its own well below this; the cap is
# only a guard against a restore that flips ops in and out, and `restore_passes.cap_hit` says when it bit.
_RESTORE_PASS_CAP = 8


def _byte_digests(before: dict[str, bytes], now: dict[str, bytes],
                  paths: list[str]) -> dict[str, dict]:
    """Per-path `(digest, length)` before and after, so a stop can be adjudicated from the run JSON."""
    def d(b: bytes | None) -> dict:
        return {"missing": True} if b is None else {
            "sha": hashlib.sha256(b).hexdigest()[:12], "len": len(b)}
    return {p: {"before": d(before.get(p)), "after": d(now.get(p))} for p in paths[:20]}


def judge_bytes(before: dict[str, bytes], now: dict[str, bytes], lost_paths: set[str],
                excused: set[str]) -> tuple[list[str], list[str]]:
    """The recoverability predicate, extracted so it can be tested without driving a repo.

    Returns `(drifted, behind_lost)`: files whose bytes changed and are not explained by a resurrected
    op, and the subset of those that a still-missing op wrote. `behind_lost` non-empty is the hard stop.

    This is a function rather than four lines inside the probe because all six of this work package's
    calibration errors were in these four lines, and every one of them was found by a repo run that
    happened to wander into the right state. `--selftest` exercises it directly instead."""
    drifted = sorted(p for p in set(before) | set(now)
                     if before.get(p) != now.get(p) and p not in excused)
    return drifted, sorted(set(drifted) & lost_paths)


def selftest() -> int:
    """Positive and negative controls for `judge_bytes`. Both directions, per R4.

    The end-to-end `--inject-loss` control cannot do this job on its own: it degrades the repo (an
    unrestored revert leaves the tree dirty, so every later revert refuses with the uncommitted-changes
    guard) and most sampled reverts turn out byte-neutral anyway, so the predicate is rarely reached with
    real loss in front of it. These cases reach it every time."""
    A, B = {"f.py": b"one\n"}, {"f.py": b""}
    cases = [
        # name,                     before, now, lost_paths, excused, want_drift, want_stop
        ("loss behind a lost op",   A, B, {"f.py"}, set(),     ["f.py"], ["f.py"]),
        ("clean round trip",        A, A, {"f.py"}, set(),     [],       []),
        ("resurrection is excused", A, B, set(),    {"f.py"},  [],       []),
        ("drift, nothing missing",  A, B, set(),    set(),     ["f.py"], []),
        ("layout-only op counts",   A, B, {"f.py"}, set(),     ["f.py"], ["f.py"]),
        ("new file is not loss",    A, {**A, "g.py": b"x"}, set(), set(), ["g.py"], []),
        ("deleted file is loss",    {**A, "g.py": b"x"}, A, {"g.py"}, set(), ["g.py"], ["g.py"]),
    ]
    bad = 0
    for name, before, now, lost, exc, want_d, want_s in cases:
        d, s = judge_bytes(before, now, lost, exc)
        ok = d == want_d and s == want_s
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {name}"
              + ("" if ok else f"  drift={d} want={want_d}  stop={s} want={want_s}"))
    print(f"judge_bytes selftest: {len(cases) - bad}/{len(cases)} pass")
    return (1 if bad else 0) or _selftest_resurrection_kind()


def _selftest_resurrection_kind() -> int:
    """Controls for `resurrection_kind`, both directions. It decides which resurrections get named a
    defect and which get named a design consequence, which is the same kind of power `judge_bytes` has
    over the hard stop -- so it gets the same treatment. The two `content` cases are the two real ops
    from the frozen baseline, byte-for-byte: `Service.__init__`'s body and `app.py`'s `if __name__`
    block sitting in the residue after `run`."""
    import tempfile
    root = Path(tempfile.mkdtemp(prefix="res-kind-"))
    (root / ".sgt/ops").mkdir(parents=True)

    def write(name: str, footprint: dict[str, bytes | None]) -> str:
        (root / ".sgt/ops" / name).write_text(json.dumps({
            "footprint": {s: [None, "x"] for s in footprint},
            "images": {s: v.hex() for s, v in footprint.items() if v is not None},
        }))
        return name

    HEAD = "\x00HEAD\x00"
    cases = [
        ("anchor, FIRST sentinel",   {"a.py::__anchor__::f": b"\x00FIRST\x00"},          "layout"),
        ("anchor, predecessor name", {"a.py::__anchor__::g": b"f"},                      "layout"),
        ("residue, blank",           {"a.py::__residue__::f": b"\n\n"},                  "layout"),
        ("residue, leading sentinel", {f"a.py::__residue__::{HEAD}": HEAD.encode()},     "layout"),
        ("anchor + blank residue",   {"a.py::__anchor__::f": b"\x00FIRST\x00",
                                      "a.py::__residue__::f": b"\n"},                    "layout"),
        ("residue with module code", {"app.py::__residue__::run":
                                      b'\n\n\nif __name__ == "__main__":\n    run()\n'}, "content"),
        ("nested entity",            {"service.py::Service.__init__":
                                      b"def __init__(self, name):\n        self.name = name"},
                                                                                         "content"),
        ("plain entity",             {"a.py::f": b"def f(): pass\n"},                    "content"),
        ("mixed layout + entity",    {"a.py::__anchor__::f": b"\x00FIRST\x00",
                                      "a.py::f": b"def f(): pass\n"},                    "content"),
        ("residue with no image",    {"a.py::__residue__::f": None},                     "layout"),
    ]
    bad = 0
    for i, (name, footprint, want) in enumerate(cases):
        got = resurrection_kind(root, write(f"{i:064d}", footprint))
        bad += got != want
        print(f"  {'ok  ' if got == want else 'FAIL'} {name}"
              + ("" if got == want else f"  got={got} want={want}"))
    # An op the store cannot produce must never be excused as layout.
    got = resurrection_kind(root, "deadbeef" * 8)
    bad += got != "content"
    print(f"  {'ok  ' if got == 'content' else 'FAIL'} missing op file is content")
    print(f"resurrection_kind selftest: {len(cases) + 1 - bad}/{len(cases) + 1} pass")
    return 1 if bad else 0


def target_kind(repo: Path, target: str | None) -> str | None:
    """`entity`, `layout`, or None when the record's target is not an op id (a feature id, a filename).

    Harness calibration error #8. Targets are drawn uniformly from the live ideal, which contains
    `__anchor__` and `__residue__` ops as first-class members -- so a share of every sweep reverts a
    *blank line's* op, which is not an operation any user issues. F97c came out of exactly one of those,
    and 7 of the 7 violations in the seed-14 re-run were of a class only layout targets can produce. A
    rate pooled across both kinds answers no question: "sgt refuses/mangles X% of operations" means one
    thing for edits a person makes and another for addressing whitespace by op id. So classify every
    target and let the table split. Not a filter -- the operations stay in the sweep, because they are
    addressable and a tool should not fall over on them; only the accounting changes.

    Shares `resurrection_kind`'s rule so the two classifications cannot disagree: anchors are metadata
    by construction, residues are judged by bytes (a residue can carry module-level user code, which is
    content), anything else is content."""
    if not target:
        return None
    d = repo / ".sgt/ops"
    if not d.is_dir():
        return None
    hits = [p.name for p in d.iterdir() if p.name.startswith(target)]
    if len(hits) != 1:
        return None
    return "layout" if resurrection_kind(repo, hits[0]) == "layout" else "entity"


def blank_tracked(repo: Path) -> set[str]:
    """Tracked files whose content is nothing but whitespace. Not `size == 0`: a revert that empties
    a file leaves a lone `\\n` behind, which the first version of this check walked straight past."""
    return {p for p in tracked_files(repo)
            if (repo / p).is_file() and not (repo / p).read_bytes().strip()}


def orphan_layout(repo: Path) -> set[str]:
    """Live `__residue__` symbols whose entity is no longer live -- a dead symbol's trailing gap,
    still in the ideal and still splicing blank lines into the file.

    F97. This is the class every existing oracle is blind to, and the blindness is structural, not an
    oversight: the verb *materializes its own composition*, so `fsck --tree` compares two copies of
    the same wrong answer and reports 0 drifted, `fsck` finds every op individually well-formed, and
    `status` prints 100% coverage. Measured on a live specimen (`revert --take-dependents`, F97b,
    unfixed): all three green over `b'def other():\\n    return 2\\n\\n\\n\\n'`.

    Residues only. Anchors legitimately outlive their entity -- they are never closed by design, which
    is F93's decision and F96's cause -- so including them fires on 2 of 18 corpus shapes at init.
    Residues do close (`salted_bottom`), so an orphan is a real defect. Validated both directions
    before being armed: fires on the specimen, silent on all 18 shapes at init.

    Pure file reads, no sgt import: this file drives sgt only as a subprocess so nothing it measures
    can be perturbed by in-process caches, and the chain walk below is an *independent* implementation
    of `order._ordered_chains` rather than a call into it -- an oracle that shares the code it is
    checking cannot catch a bug in that code. `BOTTOM` is `\\u22a5`, bare or `\\u22a5@<sha>`
    (`op.is_bottom`).

    The first version computed the tip as `afters - befores`, which is wrong and was caught by
    `_case_revert_to_original`: an edit back to an earlier byte-image makes the chain
    `None->A, A->B, B->A`, every after is also a before, the set difference is empty, and a live symbol
    reads as dead. Walk the chain from its birth instead. Anything ambiguous -- two births (a fork), no
    birth in the ideal, a cycle -- is treated as live, so this oracle under-reports rather than
    accusing."""
    chains: dict[str, list[tuple]] = {}
    for op_id in sorted(ideal_ids(repo)):
        try:
            payload = json.loads((repo / ".sgt/ops" / op_id).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for sym, pair in (payload.get("footprint") or {}).items():
            if isinstance(pair, list) and len(pair) == 2:
                chains.setdefault(sym, []).append((pair[0], pair[1]))

    def live(sym: str) -> bool:
        steps = chains.get(sym)
        if not steps:
            return False                       # no op in the ideal writes it: not present
        births = [a for b, a in steps if b is None]
        if len(births) != 1:
            return True                        # fork or no birth here -- do not accuse
        by_before = {b: a for b, a in steps if b is not None}
        at, seen = births[0], set()
        while at in by_before and at not in seen:
            seen.add(at)
            at = by_before[at]
        return not (at is not None and (at == "⊥" or at.startswith("⊥@")))

    out = set()
    for sym in chains:
        path, sep, rest = sym.partition("::__residue__::")
        if not sep or not rest or "\x00" in rest:   # the end-of-file gap belongs to no entity
            continue
        if live(sym) and not live(f"{path}::{rest}"):
            out.add(sym)
    return out


HARD_FSCK_FIELDS = ("bad_hash", "corrupt", "invalid_ideals", "unreachable_witnesses",
                    "mixed_versions")
ADVISORY_FSCK_FIELDS = ("chain_gaps", "pending_chain_gaps", "pending_land", "op_index_stale")


@dataclass
class Ctx:
    repo: Path
    seen_store: set[str] = field(default_factory=set)
    seen_shas: list[str] = field(default_factory=list)
    blank_at_init: set[str] = field(default_factory=set)
    orphans_seen: set[str] = field(default_factory=set)
    work: Path | None = None
    fsck_advisory: dict[str, set] = field(default_factory=dict)
    drift_seen: set[str] = field(default_factory=set)
    settles: int = 0
    log: list[dict] = field(default_factory=list)
    # (store size when built, creating op ids). Rebuilt whenever the store grows, since a save mints
    # new ops mid-sweep; ops are append-only here, so an unchanged count means an unchanged store.
    creator_cache: tuple[int, frozenset[str]] | None = None

    def creators(self) -> frozenset[str]:
        size = len(store_ids(self.repo))
        if self.creator_cache is None or self.creator_cache[0] != size:
            self.creator_cache = (size, creating_ops(self.repo))
        return self.creator_cache[1]

    def observe(self) -> None:
        self.seen_store |= store_ids(self.repo)
        sha = head_sha(self.repo)
        if sha and sha not in self.seen_shas:
            self.seen_shas.append(sha)


def check(ctx: Ctx) -> list[Violation]:
    repo, bad = ctx.repo, []

    # First, and returning immediately: confirm nobody else is writing this work directory
    # (instrument error #18). A collision presents as lost data -- a deleted repo empties the op store
    # and orphans every commit -- so it has to be excluded by name before any loss oracle is believed,
    # not argued about after a hard stop has already been recorded.
    if ctx.work is not None:
        owner = ctx.work / OWNER
        mine = str(os.getpid())
        if not owner.is_file() or owner.read_text().strip().split()[:1] != [mine]:
            return [Violation("harness_collision",
                              f"{owner} no longer names this run (pid {mine}); another process is "
                              f"writing {ctx.work}. Every oracle below it is unreliable.", False)]

    rc, out, err = sgt(repo, "advanced", "fsck", "--json")
    if rc != 0:
        bad.append(Violation("fsck", f"exit {rc}: {(err or out).strip()[:400]}", False))
    else:
        try:
            f = json.loads(out)
        except json.JSONDecodeError:
            bad.append(Violation("fsck", f"non-JSON output: {out[:200]!r}", False))
        else:
            # Fail on sgt's own definition of unhealthy, not on "any field non-empty". `store.py`
            # sets `ok = not (bad_hash or corrupt or invalid_ideals or unreachable or mixed)` and
            # documents chain_gaps / pending_land / op_index_stale / pending_chain_gaps as advisory
            # and self-healing. The first version of this check failed on all of them, and one
            # benign `chain_gaps` entry that appeared at op 74 then re-fired on all 2,400 remaining
            # ops -- a sticky advisory state counted 2,400 times is not a violation count.
            if not f.get("ok"):
                hard = {k: v for k, v in f.items() if k in HARD_FSCK_FIELDS and v}
                bad.append(Violation("fsck", json.dumps(hard)[:400] or "ok=false", False))
            # Calibration error #9b, the same defect one oracle over. Reporting "each distinct advisory
            # state once" only dedupes if the state's identity is stable while the condition is. These
            # entries are `<path-or-symbol>@<sha>`, and every operation writes a witness commit, so the
            # shas move even when the set of entities with a chain gap does not. On fixtures the field is
            # empty and this never showed; on the first real repo `chain_gaps` fired on 5 of 5 operations,
            # which would have made every real-repo row in the sweep ~100% flagged and the pooled headline
            # meaningless. Dedupe on the entity, dropping the version: a new sha for a gap already
            # reported is the same advisory condition, not a new one.
            for k, v in f.items():
                if k not in ADVISORY_FSCK_FIELDS or not v:
                    continue
                keys = {str(e).rsplit("@", 1)[0] for e in v}
                fresh = keys - ctx.fsck_advisory.setdefault(k, set())
                ctx.fsck_advisory[k] |= keys
                if fresh:
                    # `live` is here because deduping on the entity makes healing unobservable, and
                    # `store.py` calls these fields "advisory and self-healing". If the live set only ever
                    # grows, that claim is wrong and the artifact should be able to say so without a re-run.
                    bad.append(Violation(f"fsck_advisory_{k}",
                                         f"{len(fresh)} new, {len(keys)} live: "
                                         f"{json.dumps(sorted(fresh)[:4])}"[:300],
                                         False))

    # Drift is sticky too -- it persists until a `log --refresh` or a `save` resolves it -- so report
    # each *path* once, the way phantoms and orphans below are reported once.
    #
    # Harness calibration error #9. The first version compared the whole output against the previous
    # output and reported it whenever the two differed. That is correct while the drift set is a sticky
    # constant, which is all the fixtures ever produced. On the first real repository the set *grows*:
    # 2 paths right after init, then 4, 7, 8, 14 over five operations. Every one of those reports was
    # counted as a fresh violation and every one of them re-named its predecessors' paths, including the
    # two that drifted before any operation ran. So the instrument charged operations for drift they did
    # not cause and charged five operations for what were three new paths. Diffing the path set fixes
    # both at once, and the init baseline is now subtracted rather than merely printed.
    #
    # Accepted limitation, stated because the fix creates it: a path that drifts, is settled by a
    # `save`, and drifts again is reported only the first time. That is the same trade `orphans_seen`
    # and `blank_at_init` already make, and it errs toward under-reporting, which is the safe direction
    # for a number the paper will quote.
    rc, out, err = sgt(repo, "advanced", "fsck", "--tree")
    if rc != 0 or "0 drifted" not in out:
        paths = {ln.split("drift: ", 1)[1].split(" — ")[0].strip()
                 for ln in (out + err).splitlines() if "drift: " in ln}
        fresh = paths - ctx.drift_seen
        ctx.drift_seen |= paths
        # No parsed path means the failure is not per-path drift (a crash, a new message shape); report
        # it on the old whole-output basis rather than swallowing it.
        if fresh or not paths:
            detail = (f"{len(fresh)} newly drifted path(s): {sorted(fresh)[:4]}"
                      if fresh else (out + err).strip()[:400])
            bad.append(Violation("fsck_tree", f"exit {rc}: {detail}", False))

    store, ideal = store_ids(repo), ideal_ids(repo)
    dangling = ideal - store
    if dangling:
        bad.append(Violation("ideal_subset_of_store",
                             f"{len(dangling)} ideal op(s) absent from the store: "
                             f"{sorted(dangling)[:3]}", True))

    lost = ctx.seen_store - store
    if lost:
        bad.append(Violation("store_monotone",
                             f"{len(lost)} op(s) vanished from the store: {sorted(lost)[:3]}", True))

    # Finding 4, as an oracle. Reverting the only substantive op in a file leaves the path behind
    # as a *zero-byte tracked file*, in the working tree and in the witness commit, and
    # `fsck --tree` reports 0 drifted because the fold's image for that path is empty too -- both
    # sides agree on a file that should not exist. No bytes are lost (the op is still in the store),
    # so this is not the hard stop; it is a phantom that will be committed, and for Python an
    # importable module with none of its symbols.
    phantoms = blank_tracked(repo) - ctx.blank_at_init
    if phantoms:
        bad.append(Violation("no_empty_phantom",
                             f"blank tracked file(s) left behind: {sorted(phantoms)[:4]}",
                             False))
        ctx.blank_at_init |= phantoms  # report each phantom once; don't drown the rest of the run

    orphans = orphan_layout(repo) - ctx.orphans_seen
    if orphans:
        bad.append(Violation("orphan_layout",
                             f"{len(orphans)} dead symbol(s) left their trailing gap live in the "
                             f"ideal, so the fold still splices their blank lines: "
                             f"{sorted(orphans)[:4]}", False))
        ctx.orphans_seen |= orphans   # same dedupe argument as the phantoms above

    for sha in ctx.seen_shas:
        if git(repo, "cat-file", "-e", f"{sha}^{{commit}}")[0] != 0:
            bad.append(Violation("commits_reachable", f"commit {sha[:12]} is gone", True))
            break

    return bad


# ---------------------------------------------------------------------- operations

BODY_LINE = "    _v4_{n} = {n}\n"


def _py_files(repo: Path) -> list[Path]:
    rc, out, _ = git(repo, "ls-files")
    return [repo / p for p in out.split() if p.endswith(".py")]


def op_edit_save(ctx: Ctx, rng: random.Random, n: int) -> dict:
    """Touch one symbol's body and save. The mutation is a fresh assignment inside the first
    `def` of a random tracked .py file -- enough to mint a `rework` op, small enough that the
    file stays parseable, which keeps the *other* verbs on the interesting path."""
    files = _py_files(ctx.repo)
    if not files:
        return {"op": "edit_save", "skipped": "no tracked .py file"}
    f = rng.choice(files)
    try:
        lines = f.read_text().splitlines(keepends=True)
    except UnicodeDecodeError:
        return {"op": "edit_save", "skipped": f"{f.name} is not utf-8"}
    at = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("def ")), None)
    if at is None:  # no entity to rework -- add one, so an emptied file still generates ops
        # Terminate the last line first. Without this the appended `def` lands on the tail of an
        # unterminated line (`    return 2  # noteddef v4_added_20():`), which comments the new
        # function out -- so the op the harness thinks it minted does not exist, and every later
        # claim about that file is about a different file than the one in the log.
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"def v4_added_{n}():\n    return {n}\n")
        kind = "add"
    else:
        lines.insert(at + 1, BODY_LINE.format(n=n))
        kind = "rework"
    f.write_text("".join(lines))
    rc, out, err = sgt(ctx.repo, "save", "-m", f"v4 edit {n}")
    return {"op": "edit_save", "target": f"{f.name} ({kind})", "rc": rc,
            "out": out[-300:], "err": err[-300:]}


def op_add_file(ctx: Ctx, rng: random.Random, n: int) -> dict:
    """A new module holding exactly one function, then save. This variant exists because of the
    plan's sanity check: the known-open Finding 4 (a revert that empties a file instead of removing
    it) is only reachable when the reverted op is the *only* substantive op in its file, and every
    corpus fixture file has several. Without this the generator never visits that state and the
    harness would report a clean run it did not earn."""
    path = ctx.repo / f"v4_mod_{n}.py"
    path.write_text(f"def only_symbol_{n}():\n    return {n}\n")
    rc, out, err = sgt(ctx.repo, "save", "-m", f"v4 add module {n}")
    return {"op": "add_file", "target": path.name, "rc": rc, "out": out[-300:], "err": err[-300:]}


def _revert(ctx: Ctx, rng: random.Random, extra: list[str]) -> dict:
    """Half the draws are weighted toward ops that create a code symbol, half stay uniform.

    Pre-registered for V4-R. Creation is what gates `broken_references` and `kept_conflicts` -- see
    `creating_ops` for the measurement and for the two weightings this replaced -- and creation ops are
    only 2.7-5.2% of a live ideal, so a uniform sweep spends 95%+ of its reverts on shapes where the
    guard cannot fire by construction. Weighting every draw would be worse: the uniform draw is what
    finds everything else revert breaks, F35 among it, so the arm would trade one measurement for
    several. The mode is recorded on every entry so aggregation can split the two populations instead of
    reporting a blended rate.

    The fallback is not hypothetical: a repository whose live ideal holds no creation op falls back to
    uniform and says which it did."""
    live = sorted(ideal_ids(ctx.repo))
    if not live:
        return {"op": "revert", "skipped": "empty ideal"}
    draw = "uniform"
    if rng.random() < 0.5:
        creators = sorted(ctx.creators() & set(live))
        if creators:
            live, draw = creators, "weighted"
        else:
            draw = "uniform (no creation op live)"
    target = rng.choice(live)
    rc, out, err = sgt(ctx.repo, "revert", target, "--yes", *extra)
    return {"op": "revert" + ("".join(" " + e for e in extra)), "target": target[:12], "draw": draw,
            "rc": rc, "out": out[-300:], "err": err[-300:]}


def op_revert(ctx: Ctx, rng: random.Random, n: int) -> dict:
    return _revert(ctx, rng, [])


def op_revert_keep_dependents(ctx: Ctx, rng: random.Random, n: int) -> dict:
    return _revert(ctx, rng, ["--keep-dependents"])


def op_restore(ctx: Ctx, rng: random.Random, n: int) -> dict:
    """Restore an op the ideal has dropped. This is the recoverability verb: if the store still
    holds the op (checked separately) but `restore` cannot bring it back, the work is lost in
    practice even though the bytes are on disk."""
    # Only ops a user could actually name. About two thirds of the store is layout ops
    # (`file::__residue__::x`, `file::__anchor__::x`) and *no* read verb prints their ids -- not `log`,
    # `log --map`, `log --json`, or `advanced fsck` -- so an id like that cannot be obtained through the
    # documented interface. Feeding them to `restore` was sampling outside sgt's input space, and it is
    # what killed the seed-14 sweep at op 231: restoring a residue op whose entity is excluded rebuilds
    # the orphaned-layout state by hand, which is exactly the F35 wedge the subtract fix made
    # unreachable through revert. Recorded as F38 with its own repro; narrowed here rather than fixed
    # in sgt, because the state is not reachable by a user. (calibration error #8, 2026-08-16)
    droppable = sorted(o for o in store_ids(ctx.repo) - ideal_ids(ctx.repo) if symbols_of(ctx.repo, o))
    if not droppable:
        return {"op": "restore", "skipped": "nothing addressable excluded"}
    target = rng.choice(droppable)
    before = ideal_ids(ctx.repo)
    rc, out, err = sgt(ctx.repo, "restore", target, "--yes")
    after = ideal_ids(ctx.repo)
    return {"op": "restore", "target": target[:12], "rc": rc, "out": out[-300:], "err": err[-300:],
            "came_back": target in after, "ideal_delta": len(after) - len(before)}


def op_undo(ctx: Ctx, rng: random.Random, n: int) -> dict:
    rc, out, err = sgt(ctx.repo, "undo")
    return {"op": "undo", "rc": rc, "out": out[-300:], "err": err[-300:]}


def op_feature_rename(ctx: Ctx, rng: random.Random, n: int) -> dict:
    fs = sorted(features(ctx.repo))
    if not fs:
        return {"op": "feature_rename", "skipped": "no features"}
    target = rng.choice(fs)
    rc, out, err = sgt(ctx.repo, "feature", "rename", target, f"v4 label {n}")
    return {"op": "feature_rename", "target": target[:14], "rc": rc,
            "out": out[-300:], "err": err[-300:]}


def op_feature_merge(ctx: Ctx, rng: random.Random, n: int) -> dict:
    fs = sorted(features(ctx.repo))
    if len(fs) < 2:
        return {"op": "feature_merge", "skipped": f"{len(fs)} feature(s)"}
    a, b = rng.sample(fs, 2)
    rc, out, err = sgt(ctx.repo, "feature", "regroup", "merge", a, b)
    return {"op": "feature_merge", "target": f"{a[:14]}<-{b[:14]}", "rc": rc,
            "out": out[-300:], "err": err[-300:]}


def op_feature_split(ctx: Ctx, rng: random.Random, n: int) -> dict:
    fs = sorted(features(ctx.repo))
    if not fs:
        return {"op": "feature_split", "skipped": "no features"}
    target = rng.choice(fs)
    rc, out, err = sgt(ctx.repo, "feature", "regroup", "split", target, "--apply")
    return {"op": "feature_split", "target": target[:14], "rc": rc,
            "out": out[-300:], "err": err[-300:]}


def op_revert_restore_probe(ctx: Ctx, rng: random.Random, n: int) -> dict:
    """The recoverability round trip: drop an op, then put back *everything the revert removed*,
    and require the ideal to be exactly what it was.

    Restoring only the target is the wrong assertion, and the harness's first run made that
    mistake: `revert` removes the target and everything built on it, while `restore` brings back
    the target and what *it* needs -- so the dependents legitimately stay out, and demanding the
    ideal return from one `restore` reports a design asymmetry as lost work. The property that
    actually matters is that nothing a revert removed is unreachable afterwards, which is this.

    The assertion is one-sided for the same reason, learned the same way (seed 14 on
    `ts_export_decorated`): the round trip can end with *more* ops than it started with, because a
    restore's prerequisite closure pulls back ops that were excluded before the probe ran -- there,
    an op reverted two steps earlier came back uninvited. That is an addition, not a loss, so the
    hard-stop oracle is `before - after` only; `after - before` is recorded separately as
    `resurrected` and reported, because "restore silently re-includes work you reverted" is a
    finding about agreement, not about durability.

    **The assertion is on bytes, not on op ids** -- the third correction to this probe, and the one
    that matters most (2026-08-16). Asserting op-set equality stopped two runs as "recoverability"
    violations, and neither was one. On `class_with_methods` the op that would not come back composed
    *byte-identical* output either way, because it was an inverse splice whose effect a later op had
    already superseded: a set difference with no observable content behind it. On `ts_export_decorated`
    16 bytes really were missing after `restore <op-id>` refused -- and `sgt restore <file::symbol>`
    brought them straight back. An oracle that calls both of those "lost work" cannot be trusted when
    it says work was lost, which is the only thing it exists to say. So: compare tracked bytes, and
    when they differ, walk the documented recovery ladder (restore by id, then restore by symbol)
    before deciding. Only bytes that survive the whole ladder are a hard stop; a state the id form
    refuses and the symbol form recovers is still reported, at the severity it earns.

    **Each rung runs to a fixed point** -- the fourth correction (calibration error #7, 2026-08-16), and
    the one that stopped a sweep. `restore <op-id>` refuses order-dependently: three ops that a single
    pass reported as unrecoverable all came back, byte-exact, when the same calls were repeated after
    their siblings landed. The claim this probe is calibrated against is therefore *reachable by retry*:
    everything a revert removed must be restorable, possibly needing repeated passes. A refusal that a
    later pass clears is not loss -- it is a usability defect (F60: the refusal advises a
    version-combining `sgt resolve` when a retry returns the exact prior bytes) and it is reported as
    one. Only a state that is still short of bytes when both rungs stop making progress is a hard stop."""
    live = sorted(ideal_ids(ctx.repo))
    if not live:
        return {"op": "revert_restore_probe", "skipped": "empty ideal"}
    target = rng.choice(live)
    before, before_bytes = ideal_ids(ctx.repo), tracked_bytes(ctx.repo)
    rc1, out1, err1 = sgt(ctx.repo, "revert", target, "--yes")
    removed = sorted(before - ideal_ids(ctx.repo))
    fails, restore_out, id_passes = [], [], 0
    # Positive control (`--inject-loss`): suppress the entire recovery ladder, so the bytes the revert
    # removed really are gone and a correct oracle must say so. Every one of the six calibration errors
    # in this probe was an over-report; not one run has ever shown it fires when content is actually
    # unreachable. R4 asks for both error directions, and an oracle that has only ever been wrong in one
    # direction is not calibrated -- it is just quiet. Never set during a real sweep.
    wanted = [] if INJECT_LOSS else [target, *(o for o in removed if o != target)]
    # One pass is not the ladder's first rung, it is half of it (calibration error #7, 2026-08-16).
    # `sgt restore <id>` refuses *order-dependently*: an op whose symbol still forks against the frontier
    # is rejected, and the identical call succeeds once its siblings are back. That refusal stopped sweep B
    # as `revert_restore_bytes_lost` on three ops which all restored, byte-exact, on a second pass. The
    # claim under test is reachable-by-retry, so the rung is "repeat until a pass admits nothing new" and
    # only the fixed point is judged. The order-dependence itself is a finding (F60), not loss.
    while wanted:
        at_pass_start = ideal_ids(ctx.repo)
        fails, restore_out = [], []          # keep the settled pass, not the noisy first one
        for op in wanted:
            rc2, out2, err2 = sgt(ctx.repo, "restore", op, "--yes")
            restore_out.append(f"{op[:12]}: {out2.strip()[-200:]}")
            if rc2 != 0:
                fails.append(f"{op[:12]} rc={rc2} {(err2 or out2).strip()[-120:]}")
        id_passes += 1
        if ideal_ids(ctx.repo) == at_pass_start or id_passes >= _RESTORE_PASS_CAP:
            break
    after = ideal_ids(ctx.repo)
    lost, resurrected = sorted(before - after), sorted(after - before)
    # Byte drift after a round trip has two innocent causes and one guilty one, and the whole difficulty
    # of this oracle is telling them apart without suppressing the guilty case.
    #
    # Innocent: a restore pulls back ops reverted *earlier* in the run (`resurrected`), so the file ends up
    # with more content than the snapshot. This comment used to say "prerequisite closure" does that, and
    # that was an excuse I wrote into the instrument without checking the arithmetic: every id restored here
    # was live in `before`, `before` is downward-closed, so its closure is *inside* `before`, while
    # `resurrected` is by definition outside it -- closure cannot produce a single one. Measured on the
    # surviving sweep repos, `downset_in(X, provenance)` and `downset_in(X, whole store)` reach 0 ops
    # outside the live ideal for all 398 and 436 live ops. The real cause is the layout-sibling pull-in
    # (`verbs.py:227-245`, F35); `resurrection_kind` above splits it. Gating the entire check on
    # `lost` was the first attempt at handling that (calibration error #6) and it was too blunt -- it also
    # blinded the oracle to a forward subtraction, which changes bytes while leaving the target op *live*
    # in the ideal, i.e. drift with `lost` empty and content genuinely gone. So: subtract the resurrected
    # ops' own files from the comparison instead of dropping the comparison. Same effect on #6, no
    # blindness.
    #
    # Guilty: any remaining difference. If it lands in a file a *missing* op wrote, content behind an op
    # that will not come back is unreachable -- the hard stop. If it lands anywhere else, the round trip
    # returned the op set but not the bytes; that is either loss through a path I have not reproduced by
    # hand or non-deterministic composition. Both are findings, neither is a hard stop yet: six
    # calibration errors in one direction have earned this oracle a probation period, and a new class
    # reports until a human repro promotes it.
    # Recomputed on every judgement rather than once, because the symbol rung below can bring an op back:
    # a `lost_paths` frozen before it would blame a still-different file on an op that is no longer
    # missing, which is an over-report in the same direction as every other error this probe has made.
    def _judge() -> tuple[list[str], list[str], list[str], list[str]]:
        now = ideal_ids(ctx.repo)
        lost_, res_ = sorted(before - now), sorted(now - before)
        lost_paths = {p for op in lost_ for p in paths_of(ctx.repo, op)}
        # Split, don't narrow. Excusing only *layout* resurrection would push a content resurrection's
        # real byte difference into the drift branches -- including the fatal one, if a still-missing op
        # happens to have written that file -- and a spurious hard stop is the one failure this harness
        # cannot afford. So the excusal stays wide and the case gets its own name instead of being hidden
        # inside it.
        excused = {p for op in res_ for p in paths_of(ctx.repo, op)}
        d, bl = judge_bytes(before_bytes, tracked_bytes(ctx.repo), lost_paths, excused)
        return lost_, res_, d, bl

    lost, resurrected, drifted, behind_lost = _judge()
    by_symbol, sym_passes = [], 0
    while rc1 == 0 and drifted and lost and not INJECT_LOSS:
        # The id rung settled and bytes are still missing. Try the form the CLI does *not* mention in
        # that refusal -- also to a fixed point, for the same order-dependence reason.
        at_pass_start = ideal_ids(ctx.repo)
        for op in lost:
            for sym in symbols_of(ctx.repo, op):
                rc3, out3, err3 = sgt(ctx.repo, "restore", sym, "--yes")
                by_symbol.append(f"{sym} rc={rc3} {(err3 or out3).strip()[-120:]}")
        sym_passes += 1
        lost, resurrected, drifted, behind_lost = _judge()
        if ideal_ids(ctx.repo) == at_pass_start or sym_passes >= _RESTORE_PASS_CAP:
            break
    res_content = [op for op in resurrected if resurrection_kind(ctx.repo, op) == "content"]
    after = ideal_ids(ctx.repo)   # the whole ladder's fixed point, not the id rung's
    rec = {"op": "revert_restore_probe", "target": target[:12], "rc": rc1,
           "removed": len(removed), "restored": len(after - (before - set(removed))),
           "returned": after == before, "restore_failures": fails,
           "resurrected": [o[:12] for o in resurrected],
           "resurrected_content": [o[:12] for o in res_content], "drifted_files": drifted,
           "restore_by_symbol": by_symbol,
           "out": out1[-200:], "err": err1[-200:], "restore_out": restore_out}
    rec["drift_behind_lost_op"] = behind_lost
    rec["restore_passes"] = {"by_id": id_passes, "by_symbol": sym_passes,
                             "cap_hit": max(id_passes, sym_passes) >= _RESTORE_PASS_CAP}
    # A stop must be adjudicable from the record alone. Sweep B's was not: reproducing it took a fresh
    # clone and eight restores by hand, because the record said which files differed but not how. Digests
    # and lengths for the files in question, written only when something drifted, cost a few hundred bytes
    # per event and answer "did bytes go missing, and how many" without a repro.
    rec["byte_digests"] = _byte_digests(before_bytes, tracked_bytes(ctx.repo),
                                        sorted(set(drifted) | set(behind_lost))) if drifted else {}
    if rc1 == 0 and behind_lost:
        rec["violation"] = Violation(
            "revert_restore_bytes_lost",
            f"revert {target[:12]} then restoring everything it removed (by op id, then by symbol) "
            f"left {len(behind_lost)} file(s) different from before, and every one of them is a file a "
            f"still-missing op wrote: {behind_lost[:3]}"
            + (f"; refusals: {fails + by_symbol}" if fails or by_symbol else ""), True)
    elif rc1 == 0 and drifted:
        rec["violation"] = Violation(
            "revert_restore_unexplained_drift",
            f"revert {target[:12]} then restoring everything it removed left {len(drifted)} file(s) "
            f"different from before: {drifted[:3]}. No op is missing from the ideal and no resurrected "
            f"op wrote these files, so either the round trip returned the op set without the bytes or "
            f"composition is not deterministic. Reported, not a stop, until reproduced by hand."
            + (f"; refusals: {fails + by_symbol}" if fails or by_symbol else ""), False)
    elif rc1 == 0 and lost and by_symbol:
        rec["violation"] = Violation(
            "restore_by_id_refused",
            f"revert {target[:12]} removed {len(removed)} op(s); `restore <op-id>` refused for "
            f"{len(lost)} of them and only `restore <file::symbol>` recovered the bytes. "
            f"Refusals: {fails}", False)
    elif rc1 == 0 and lost:
        rec["violation"] = Violation(
            "revert_restore_roundtrip",
            f"revert {target[:12]} removed {len(removed)} op(s); after restoring all of them "
            f"{len(lost)} op(s) are still out of the ideal, though every tracked file composes the "
            f"same bytes as before"
            + (f"; restore refused: {fails}" if fails else ""), False)
    elif rc1 == 0 and res_content:
        rec["violation"] = Violation(
            "restore_resurrects_content",
            f"restoring what revert {target[:12]} removed also pulled back "
            f"{len(res_content)} op(s) carrying user code that were already out: "
            f"{[o[:12] for o in res_content[:3]]}", False)
    elif rc1 == 0 and resurrected:
        rec["violation"] = Violation(
            "restore_resurrects_layout",
            f"restoring what revert {target[:12]} removed also pulled back "
            f"{len(resurrected)} layout op(s) that were already out (anchors/blank residues, which a "
            f"restore must carry with its entity): {[o[:12] for o in resurrected[:3]]}", False)
    return rec


def op_revert_undo_probe(ctx: Ctx, rng: random.Random, n: int) -> dict:
    """`sgt revert` prints "`sgt undo` reverses this." after every apply. That is a contract the
    tool states in its own output, so it is checked as one: revert, undo, and the ideal must be
    byte-for-byte the set it was. Unlike the restore probe there is no asymmetry to argue about.

    Reported, not a hard stop. F33 (the first thing this probe found) drops an edit the user did not
    ask to drop, but the op is still in the store and `sgt restore <id> --yes` brings it back
    byte-exact -- verified by hand. Wrong-target is a correctness failure, not lost work, so the run
    keeps sampling; the recoverability oracles below are the ones that stop it."""
    live = sorted(ideal_ids(ctx.repo))
    if not live:
        return {"op": "revert_undo_probe", "skipped": "empty ideal"}
    target = rng.choice(live)
    before = ideal_ids(ctx.repo)
    rc1, out1, err1 = sgt(ctx.repo, "revert", target, "--yes")
    mid = ideal_ids(ctx.repo)
    if rc1 == 0 and "sgt undo" not in out1:
        # The revert declined to offer an undo -- it changed nothing, so no event was recorded and
        # there is no contract to check. Running undo here would pop an unrelated earlier edit and
        # the probe would report the tool's own honesty as a defect.
        return {"op": "revert_undo_probe", "target": target[:12], "rc": rc1,
                "removed": len(before - mid), "skipped": "revert offered no undo",
                "out": out1[-150:], "err": err1[-150:]}
    rc2, out2, err2 = sgt(ctx.repo, "undo")
    after = ideal_ids(ctx.repo)
    rec = {"op": "revert_undo_probe", "target": target[:12], "rc": rc1 or rc2,
           "removed": len(before - mid), "returned": after == before,
           "out": (out1[-150:] + " | " + out2[-150:]), "err": (err1[-150:] + err2[-150:])}
    if rc1 == 0 and after != before:
        rec["violation"] = Violation(
            "revert_undo_roundtrip",
            f"revert {target[:12]} then `sgt undo` (rc={rc2}) left the ideal changed: "
            f"-{len(before - after)} +{len(after - before)}", False)
    return rec


OPS = [
    (op_edit_save, 4),
    (op_add_file, 2),
    (op_revert, 3),
    (op_revert_keep_dependents, 2),
    (op_restore, 3),
    (op_undo, 2),
    (op_revert_restore_probe, 2),
    (op_revert_undo_probe, 2),
    (op_feature_rename, 1),
    (op_feature_merge, 1),
    (op_feature_split, 1),
]
BY_NAME = {fn.__name__: fn for fn, _ in OPS}


# ------------------------------------------------------------------------- driver


OWNER = ".harness-owner"   # written into the work dir; see claim_work() and check()


def claim_work(work: Path) -> None:
    """Refuse a work directory another live run owns, and leave a marker so a later collision is
    reported as a collision.

    Instrument error #18. Two runs pointed at the same `--work` path: the second's `rmtree` below
    deleted the first's repo *mid-run*, and the first reported `store_monotone` (52 ops vanished) and
    `commits_reachable` (a commit is gone) -- two **recoverability** violations, the plan's hard
    stop-and-ask, on a defect that was entirely mine. The harness cannot tell "sgt destroyed the store"
    from "something else deleted the directory", and a false hard stop is the most expensive kind of
    wrong reading this instrument can produce. So it now refuses instead of inferring."""
    marker = work / OWNER
    if marker.is_file():
        try:
            pid = int(marker.read_text().strip().split()[0])
        except (ValueError, IndexError, OSError):
            pid = 0
        alive = pid > 0 and subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode == 0
        if alive:
            raise SystemExit(f"{work} is owned by a live harness run (pid {pid}). Two runs sharing a "
                             f"work directory delete each other's repo mid-run and report it as lost "
                             f"data. Use a different --work path.")


def build(case: str | None, repo_src: Path | None, work: Path) -> Path:
    claim_work(work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / OWNER).write_text(f"{os.getpid()}\n")
    if case:
        from tests.laws import corpus
        return corpus.CORPUS[case].build(work)
    dest = work / repo_src.name
    subprocess.run(["git", "clone", "--quiet", "--local", str(repo_src), str(dest)],
                   check=True, capture_output=True, text=True)
    return dest


# F35 used to be handled here, by an `unwedge()` that reported the wedge and then "cleared" it with
# `sgt advanced resync`. Two things were wrong with that. resync re-derives ops from *git history*, so
# it cannot absorb the uncommitted bytes that were the whole problem -- it printed an identical
# `+2 op(s)` nine times while the repo stayed wedged, and read as a recovery in the log. And handling a
# defect keeps the run going past the point where it is still sampling. The defect is fixed in
# `subtract.py` now; what stays is the generic backstop below, which makes no claim about *why* the
# repo stopped accepting work.
STUCK = 15  # consecutive refusals that mean the repo is done accepting work, whatever the reason

INJECT_LOSS = False  # set by --inject-loss; see op_revert_restore_probe. A test of the harness, not of sgt.


def settle(ctx: Ctx) -> dict | None:
    """Do what the tool's own refusal says to do, in the order it says it, and count every time.

    sgt refuses each materializing verb while the working tree diverges from the composed ideal:
    "record them with `sgt save`, or commit / `git restore` those files, then re-run". Nothing in the op
    generator ever answers that, so a single refused save cascades -- the file stays dirty, every later
    verb refuses the same way, and the run dies at the STUCK backstop with an op count that is a lie.
    That is how the seed-14 sweep ended at op 231 of 2500.

    This is deliberately not the `unwedge()` that was deleted from this file. That one asserted a defect
    had been cleared when it had not. The differences that matter: it runs only when the tree is actually
    dirty; it runs the two commands the tool itself prints, nothing clever; every intervention is recorded
    and counted in the artifact, so "how often did a random verb sequence leave a tree needing manual
    cleanup?" stays a reportable number instead of disappearing; and if neither command settles the tree,
    that is a wedge no documented command clears and the caller stops. `git restore` is safe to use here
    for a reason that was checked by hand, not assumed: a refused `sgt save` still mints its ops into the
    store, so the discarded bytes stay reachable by `sgt restore <id>` (F38 repro).
    """
    dirty = git(ctx.repo, "status", "--porcelain")[1].strip()
    if not dirty:
        return None
    rc, out, err = sgt(ctx.repo, "save", "-m", "v4 settle")
    how = "save"
    if git(ctx.repo, "status", "--porcelain")[1].strip():
        git(ctx.repo, "restore", "--", ".")
        git(ctx.repo, "clean", "-qfd")
        how = "save refused, git restore"
    left = git(ctx.repo, "status", "--porcelain")[1].strip()
    return {"how": how, "was_dirty": dirty.splitlines()[:4], "still_dirty": left.splitlines()[:4],
            "save_rc": rc, "save_out": (err or out).strip()[-160:]}


def run(repo: Path, seed: int, count: int, script: list[str] | None, out_dir: Path,
        label: str, work: Path, source: str, kind: str) -> int:
    rc, out, err = sgt(repo, "init", ".")
    if rc != 0:
        print(f"sgt init failed: {(err or out)[-500:]}")
        return 2
    # Sampled here as well as at write time (instrument error #16). Every op spawns a fresh `sgt`
    # process, so a run that overlaps an edit under `sgt/` executes two different systems and no single
    # digest describes it -- and the end-of-run sample silently labels the whole run with the *later*
    # tree. That is how the seed-14 re-run came out carrying a digest for code that was not present for
    # 199 of its 250 ops. Record both; a mismatch marks the run unpoolable rather than leaving it to
    # be inferred from mtimes.
    version_at_start = system_version()
    ctx = Ctx(repo=repo, blank_at_init=blank_tracked(repo), work=work)
    ctx.observe()
    pre = check(ctx)
    # Recorded, not just printed (instrument error #15): without this no artifact can distinguish
    # "op i broke this" from "it was already broken at op 0". F96 is a state a repo can be *mined
    # into* -- `fsck --tree` drifts on an untouched clone -- so an init-time oracle failure is a
    # property of the input, and reading a mid-run violation without it misattributes the defect.
    init_state = [{"oracle": v.oracle, "detail": v.detail, "recoverability": v.recoverability}
                  for v in pre]
    if pre:
        print("oracles already unhappy right after init:")
        for v in pre:
            print(f"  {v.oracle}: {v.detail}")

    # Two independent streams, deliberately. Drawing the script and the ops' internal choices from
    # one Random made `--replay` diverge from the run it replayed: supplying the script skips the
    # draws that generated it, so every later choice shifts. The repro claim in this file's docstring
    # depends on this being two streams.
    pick = random.Random(seed)
    rng = random.Random(seed ^ 0x5F1E)
    # `is None`, not falsy: an empty script is a supplied script of length zero, and the `or` here
    # drew a fresh random one instead, so `--replay --prefix 0` ran 40 freshly generated ops while
    # reporting itself as a replay. The same defect as the `--prefix` test in `main`, one layer down,
    # and fixing only the outer one left the behaviour unchanged.
    chosen = [pick.choices([fn.__name__ for fn, _ in OPS],
                           weights=[w for _, w in OPS])[0] for _ in range(count)] \
        if script is None else script
    tracebacks = 0
    for i, name in enumerate(chosen):
        rec = BY_NAME[name](ctx, rng, i)
        probe = rec.pop("violation", None)
        rec["i"] = i
        rec["target_kind"] = target_kind(ctx.repo, rec.get("target"))
        if "Traceback" in (rec.get("err") or ""):
            rec["traceback"] = True
            tracebacks += 1
        ctx.observe()
        bad = check(ctx)
        if probe:
            bad.insert(0, probe)
        rec["violations"] = [{"oracle": v.oracle, "detail": v.detail,
                              "recoverability": v.recoverability} for v in bad]
        ctx.log.append(rec)
        mark = "✗" if bad else ("·" if rec.get("skipped") else "✓")
        print(f"  {i:3d} {mark} {name:24s} {rec.get('skipped') or rec.get('target') or ''}"
              f"{'  TRACEBACK' if rec.get('traceback') else ''}")
        for v in bad:
            print(f"        {'RECOVERABILITY ' if v.recoverability else ''}{v.oracle}: {v.detail}")
        if any(v.recoverability for v in bad):
            print("  STOPPING: a recoverability oracle failed. Per the plan this is a hard "
                  "stop-and-ask, not something to keep sampling past.")
            break
        # After the oracles, never before: settling changes the tree, and an oracle that ran on a settled
        # tree would be judging the cleanup instead of the op.
        st = settle(ctx)
        if st:
            rec["settled"] = st
            ctx.settles += 1
            print(f"        settled ({st['how']}): {st['was_dirty']}")
            if st["still_dirty"]:
                print("  STOPPING: the tree is still dirty after both remedies the tool prints "
                      f"(`sgt save`, then `git restore`): {st['still_dirty']}. Nothing documented "
                      "clears this, so it is a wedge, not a refusal.")
                break
        # Generic backstop for the whole F35 class: if the repo has stopped accepting anything at all,
        # the loop is no longer sampling and its op count would be a lie. Stop and say why rather than
        # keep counting refusals.
        tail = ctx.log[-STUCK:]
        if len(tail) == STUCK and all(r.get("rc") not in (0, None) for r in tail):
            print(f"  STOPPING: {STUCK} consecutive refusals — the repo accepts nothing, so further "
                  f"ops would sample nothing. Last error: {(rec.get('err') or '')[-200:]}")
            break

    out_dir.mkdir(parents=True, exist_ok=True)
    n_bad = sum(1 for r in ctx.log if r["violations"])
    # `refused` and `skipped` are reported next to `applied` on purpose: an op that exits non-zero or
    # bails out is not a sampled operation, and a run whose refusal rate is high covered far less
    # ground than its op count suggests. Without these two numbers "2,500 ops" is not a coverage
    # claim, it is a loop count.
    refused = sum(1 for r in ctx.log if r.get("rc") not in (0, None))
    skipped = sum(1 for r in ctx.log if r.get("skipped"))
    version_at_end = system_version()
    # `script_len` is what the run could have executed, which is `requested_ops` except under
    # `--replay --prefix N`. Without it, aggregate.py reads every prefix replay as a run that stopped on
    # a backstop and prints a wrong reason -- an instrument reporting a plausible cause it did not check,
    # which is the same defect shape this ledger keeps recording in sgt's own messages.
    # `kind` and `source` are what the run was built *from*; `repo` is the throwaway clone and is the
    # same shape either way. Without them an artifact cannot say whether it exercised a fixture or a
    # repository we did not write, so the arm that produced every finding worth reporting was not
    # identifiable from its own output, and `--replay` of a real-repository run raised KeyError trying
    # to look the label up in `tests.laws.corpus`. Two symptoms, one missing pair of fields.
    art = {"label": label, "seed": seed, "requested_ops": count, "script_len": len(chosen),
           "repo": str(repo), "kind": kind, "source": source,
           "system": version_at_start,
           "system_at_end": version_at_end,
           "version_mixed": version_at_start != version_at_end,
           "init_state": init_state,
           "applied": len(ctx.log), "refused": refused, "skipped": skipped,
           "with_violations": n_bad, "tracebacks": tracebacks, "settles": ctx.settles,
           "store_ops_seen": len(ctx.seen_store), "commits_seen": len(ctx.seen_shas),
           "script": chosen, "log": ctx.log}
    dest = out_dir / f"run-{label}-seed{seed}.json"
    dest.write_text(json.dumps(art, indent=1))
    print(f"{label} seed {seed}: {len(ctx.log)} ops applied, {n_bad} with an oracle failure, "
          f"{tracebacks} traceback(s), {ctx.settles} tree(s) settled → {dest}")
    if art["version_mixed"]:
        print("  WARNING: `sgt/` changed while this run was in flight, so it exercised two systems. "
              "Do not pool this artifact; re-run it.")
    return 1 if n_bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="a tests/laws/corpus.py fixture name")
    ap.add_argument("--repo", type=Path, help="an existing repo to clone and drive instead")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ops", type=int, default=40)
    ap.add_argument("--work", type=Path, default=Path("/tmp/v4-work"))
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument("--replay", type=Path, help="a run-*.json to re-run verbatim")
    ap.add_argument("--prefix", type=int, help="with --replay, stop after this many ops")
    ap.add_argument("--inject-loss", action="store_true",
                    help="positive control: skip the revert probe's recovery ladder, so the run SHOULD "
                         "hard-stop on revert_restore_bytes_lost. Proves the oracle fires. Not a sweep.")
    ap.add_argument("--selftest", action="store_true",
                    help="run the judge_bytes controls and exit; no repo is built")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    global INJECT_LOSS
    INJECT_LOSS = args.inject_loss

    script = None
    if args.replay:
        prior = json.loads(args.replay.read_text())
        # `is not None`, because `--prefix 0` is a legitimate request for zero ops and the falsy test
        # here silently replayed the entire script instead. A flag that is ignored rather than refused
        # is the failure shape this ledger keeps recording in sgt's own commands, and the instrument
        # had it too.
        script = prior["script"] if args.prefix is None else prior["script"][:args.prefix]
        args.seed = prior["seed"]
        # A run on a repository we did not write has no entry in `tests.laws.corpus`, so restoring its
        # label into `--case` made `build` raise KeyError and no real-repository run could be replayed
        # at all. Replay whatever the artifact says it was built from, and keep the old behaviour for
        # artifacts written before `kind` existed.
        if prior.get("kind") == "real" and not args.repo:
            args.repo = Path(prior["source"])
        elif not args.case:
            args.case = prior.get("source") or prior["label"]
    if not args.case and not args.repo:
        ap.error("one of --case or --repo is required")

    label = args.case or args.repo.name
    work = args.work / label
    repo = build(args.case, args.repo, work)
    source = args.case if args.case else str(args.repo)
    return run(repo, args.seed, args.ops, script, args.out, label, work,
               source, "fixture" if args.case else "real")


if __name__ == "__main__":
    raise SystemExit(main())
