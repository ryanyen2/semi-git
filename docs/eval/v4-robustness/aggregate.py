#!/usr/bin/env python3
"""Pool WP-V4 run artifacts into the table the paper reports, and refuse to pool across versions.

    python docs/eval/v4-robustness/aggregate.py [dir]

This exists because the first version of that table was assembled by hand from whatever `run-*.json`
files were in the directory. Their mtimes spanned two days of active bug-fixing, five of them predated
two of the recorded fields, and one of them was a run the ledger had already declared discarded. None
of that was visible in the table, and a per-run version stamp is worthless if the pooling step does
not check it. So this script is the check: one `system` stamp across every artifact, or a non-zero
exit naming the disagreement.

Every count is recomputed from the per-op `log`, not read from the artifact's summary keys. Where the
two disagree the script says so, because a summary key written by an older harness is exactly the kind
of number that gets copied into a paper without anybody re-deriving it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

AGG_VERSION = 2

def real_labels(d: Path) -> set[str] | None:
    """Labels of the sequences that ran on repositories we did not write, or None if unknowable.

    Each artifact now records `kind`, so read that first and take the labels of the runs that say
    `real`. Runs written before that field existed do not carry it, and for those `sweep-plan.json`
    remains the authoritative source; when neither is available, return None and let the caller say the
    split is unavailable. Guessing from the label shape (real clones happen to be named `owner__repo`)
    stays out, because it is a coincidence of naming and not a recorded fact.
    """
    recorded, saw_kind = set(), False
    for f in sorted(d.glob("run-*.json")):
        try:
            art = json.loads(f.read_text())
        except (ValueError, OSError):
            continue
        if "kind" in art:
            saw_kind = True
            if art["kind"] == "real":
                recorded.add(art["label"])
    if saw_kind:
        return recorded
    plan = d / "sweep-plan.json"
    if not plan.is_file():
        return None
    try:
        return {Path(p).name for p in json.loads(plan.read_text()).get("repos", ())}
    except (ValueError, OSError):
        return None


def counts(art: dict) -> dict:
    log = art["log"]
    return {
        "applied": len(log),
        "completed": sum(1 for r in log if r.get("rc") in (0, None) and not r.get("skipped")),
        "refused": sum(1 for r in log if r.get("rc") not in (0, None)),
        "skipped": sum(1 for r in log if r.get("skipped")),
        "flagged": sum(1 for r in log if r.get("violations")),
        "violations": sum(len(r.get("violations") or ()) for r in log),
        "tracebacks": sum(1 for r in log if r.get("traceback")),
        "settles": sum(1 for r in log if r.get("settled")),
    }


def by_target(art: dict) -> dict:
    """Split the same counts by what the operation was aimed at.

    Harness calibration error #8: targets are drawn uniformly from the live ideal, and about two thirds of
    a corpus ideal is `__anchor__`/`__residue__` ops -- whitespace and ordering facts nobody reverts. A
    pooled rate that does not split on this is not a statement about user-issuable operations, so this
    script computes the split rather than leaving it to whoever reads the table.

    `entity` is the population the paper's robustness claim is about. `layout` belongs with the §7 seam
    limitation. `other` is every operation whose target was not an op id at all -- a feature id, a
    filename, or nothing -- which is a legitimate user operation and counts with `entity` in the headline.
    """
    out = {}
    for kind in ("entity", "layout", "other"):
        rows = [r for r in art["log"]
                if (r.get("target_kind") or "other") == kind]
        out[kind] = {
            "applied": len(rows),
            "flagged": sum(1 for r in rows if r.get("violations")),
            "violations": sum(len(r.get("violations") or ()) for r in rows),
            "refused": sum(1 for r in rows if r.get("rc") not in (0, None)),
        }
    return out


def main(argv: list[str]) -> int:
    d = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent
    arts = []
    for f in sorted(d.glob("run-*.json")):
        arts.append((f, json.loads(f.read_text())))
    if not arts:
        print(f"no run-*.json under {d}")
        return 1

    unstamped = [f.name for f, a in arts if not a.get("system")]
    if unstamped:
        print("REFUSING to pool: these artifacts carry no `system` stamp, so nothing here can show "
              "they tested the same version -- and the first time this table was assembled, they had "
              f"not: {', '.join(unstamped)}")
        return 2
    mixed = [f.name for f, a in arts if a.get("version_mixed")]
    if mixed:
        print("REFUSING to pool: `sgt/` changed while these runs were in flight, so each exercised two "
              f"systems: {', '.join(mixed)}")
        return 2
    blind = [f.name for f, a in arts
             if any(r.get("target") and "target_kind" not in r for r in a["log"])]
    if blind:
        print("REFUSING to pool: these artifacts predate `target_kind`, so there is no way to tell which "
              "of their operations a user could have issued and which reverted a blank line (harness "
              f"calibration error #8): {', '.join(blind)}")
        return 2
    stamps = {}
    for f, a in arts:
        stamps.setdefault(json.dumps(a["system"], sort_keys=True), []).append(f.name)
    if len(stamps) > 1:
        print("REFUSING to pool: these artifacts did not test the same system.")
        for s, names in stamps.items():
            print(f"  {s}\n      {', '.join(names)}")
        print("Re-run the sweeps under one version, or aggregate each group separately.")
        return 2

    # This script refuses to pool across instrument versions and then, until now, stamped nothing about
    # itself. Every number the paper reports comes out of here, so an edit to the aggregator is as
    # capable of moving a published figure as an edit to the harness -- the per-sequence block below is
    # itself proof, since it reframes the same artifacts. Print the digest so a table can be traced to
    # the code that produced it.
    print(f"aggregator: version {AGG_VERSION}, sha256 "
          f"{hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]}")

    total = {k: 0 for k in counts(arts[0][1])}
    print(f"{'label':24s} {'seed':>4} {'req':>5} {'appl':>5} {'done':>5} {'ref':>4} {'skip':>4} "
          f"{'flag':>4} {'viol':>4} {'tb':>3} {'set':>4}")
    for f, a in arts:
        c = counts(a)
        for k in ("refused", "skipped"):
            if k in a and a[k] != c[k]:
                print(f"  ! {f.name}: stored {k}={a[k]}, recomputed {c[k]}")
        print(f"{a['label']:24s} {a['seed']:>4} {a['requested_ops']:>5} {c['applied']:>5} "
              f"{c['completed']:>5} {c['refused']:>4} {c['skipped']:>4} {c['flagged']:>4} "
              f"{c['violations']:>4} {c['tracebacks']:>3} {c['settles']:>4}")
        for k, v in c.items():
            total[k] += v
    print(f"{'TOTAL':24s} {'':>4} {sum(a['requested_ops'] for _, a in arts):>5} "
          f"{total['applied']:>5} {total['completed']:>5} {total['refused']:>4} {total['skipped']:>4} "
          f"{total['flagged']:>4} {total['violations']:>4} {total['tracebacks']:>3} "
          f"{total['settles']:>4}")
    # Per *sequence*, not per operation. Every rate above divides by operations, which is the wrong unit
    # for the only reader who matters: nobody runs one operation. A developer runs a session of them, so
    # the number they experience is the chance that a session of 20-50 operations contains at least one
    # violation -- and that is roughly an order of magnitude larger than the per-op rate, because
    # violations cluster within a sequence rather than spreading evenly across the pool. Reporting only
    # the per-op rate is not wrong, it is the flattering half of a true statement.
    def flagged_rows(a: dict, user_only: bool) -> int:
        return sum(1 for r in a["log"] if r.get("violations")
                   and (not user_only or (r.get("target_kind") or "other") != "layout"))

    dirty = [a for _, a in arts if flagged_rows(a, False)]
    # Restricted to user-issuable targets, for the same reason the split below excludes `layout` from the
    # robustness denominator: a sequence whose only violation reverted a blank line's op id is not a
    # session a developer could have had. Skipping this restriction inflates the per-session claim by the
    # layout-only sequences, which is the mistake the per-op rate does not make.
    duser = [a for _, a in arts if flagged_rows(a, True)]
    reals = real_labels(d)
    print(f"\nsequences pooled: {len(arts)}")
    print(f"  with >=1 violation on a user-issuable target: {len(duser)} of {len(arts)} "
          f"({len(duser) / len(arts):.0%})   <- the rate a developer meets, per session")
    print(f"  with >=1 violation on any target: {len(dirty)} of {len(arts)} "
          f"({len(dirty) / len(arts):.0%}); {len(dirty) - len(duser)} are layout-target only")
    print(f"  per-operation, for comparison: {total['flagged']} of {total['applied']} "
          f"({total['flagged'] / total['applied']:.1%})" if total["applied"] else "")
    if reals is None:
        print("  fixture/real split unavailable: no sweep-plan.json here, and an artifact does not record "
              "which arm it belongs to (instrument gap). Do not infer it from the label shape.")
    else:
        # Keyed on (label, seed): a label is not unique -- each shape runs many times under different
        # seeds, so a label-keyed set reports every sequence sharing a shape with any dirty one as dirty.
        dl = {(a["label"], a["seed"]) for a in duser}
        rn = [(a["label"], a["seed"]) for _, a in arts if a["label"] in reals]
        fn = [(a["label"], a["seed"]) for _, a in arts if a["label"] not in reals]
        print(f"  real repositories: {sum(1 for x in rn if x in dl)} of {len(rn)} dirty     "
              f"fixtures: {sum(1 for x in fn if x in dl)} of {len(fn)} dirty")

    split = {k: {m: 0 for m in ("applied", "flagged", "violations", "refused")}
             for k in ("entity", "layout", "other")}
    for _, a in arts:
        for kind, c in by_target(a).items():
            for m, v in c.items():
                split[kind][m] += v
    print(f"\n{'target':8s} {'appl':>6} {'ref':>5} {'flag':>5} {'viol':>5}   flagged share")
    for kind in ("entity", "other", "layout"):
        c = split[kind]
        share = f"{c['flagged'] / c['applied']:.1%}" if c["applied"] else "--"
        print(f"{kind:8s} {c['applied']:>6} {c['refused']:>5} {c['flagged']:>5} {c['violations']:>5}   {share:>13}")
    user = split["entity"]["applied"] + split["other"]["applied"]
    uflag = split["entity"]["flagged"] + split["other"]["flagged"]
    print(f"{'USER':8s} {user:>6} {'':>5} {uflag:>5} {'':>5}   "
          f"{(uflag / user if user else 0):>12.1%}   <- the paper's robustness denominator")
    print("`layout` operations revert a blank line's op id, which no user does. Report them with the §7 "
          "seam limitation, not in the robustness rate.")

    # Against `script_len`, not `requested_ops`: a `--replay --prefix N` run executes fewer ops than it
    # requested by design, and calling that a backstop stop is the instrument inventing a cause.
    # `if "script_len" in a`, not `or`: a zero-op prefix replay records `script_len` 0 truthfully, and
    # the `or` read that as "absent" and substituted 40, so the one run that executed exactly what it
    # was asked to would have been reported as truncated. Third instance of the same falsy-zero
    # confusion in this instrument, counting the two in `harness.py`.
    planned = lambda a: a["script_len"] if "script_len" in a else a["requested_ops"]
    truncated = [f"{a['label']}/seed{a['seed']} ({len(a['log'])} of {planned(a)})"
                 for _, a in arts if len(a["log"]) < planned(a)]
    if truncated:
        print(f"\ntruncated ({len(truncated)}): {'; '.join(truncated)}")
        print("A truncated run stopped on a backstop or a hard stop, not on its op budget. Its reason "
              "is the last STOPPING line in its log; report it, do not report the requested count.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
