#!/usr/bin/env python3
"""WP-V1: census one study repo's sgt record against its build log.

    python docs/eval/v1-census/census.py <repo> --episodes <map.json> --out <dir>

Reads only what sgt itself prints (`sgt log --json`) plus the op footprints in
`.sgt/ops`, which is the same data that surface is derived from. Emits one row per
episode: which features the episode's ops landed in, and the flags split / lump /
mislabel / miss.

An op's footprint names symbols. Three record kinds share that space:
substantive edits, `__anchor__` records (unchanged context sgt pins), and
`__residue__` records (whole-file remainder). Only substantive records are counted
toward an episode's footprint; the others are reported separately, because counting
them makes every episode look scattered when nothing moved.

The `mislabel` flag (WP-V1 step 2 names it; the first version of this script declared
it and never computed it). Definition, fixed before running: a feature's *label
coverage* is the fraction of its member symbols whose file-or-qualname contains a
content token of its label, lowercased, tokens of 4+ characters, stopwords dropped.
A feature with 5 or more members and coverage below 0.34 is flagged `mislabel`: the
name describes a minority of what the feature holds, so a reader who trusts the name
is wrong about most of it. Both directions are reported, per R4: `label-covers-all`
lists features whose coverage is 1.0, so the flag is not just an accusation counter.

This rule was written after seeing one bad case (`Course Search` holding the whole CLI
command surface), so it is not a blind detector. It is a stated rule applied uniformly
to every feature in both repos, and its threshold is not tuned per repo.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path


def sgt_log(repo: Path) -> dict:
    proc = subprocess.run(["sgt", "log", "--json"], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"sgt log --json failed in {repo}:\n{proc.stderr}")
    return json.loads(proc.stdout)


def classify(symbol: str) -> str:
    if "::__anchor__::" in symbol:
        return "anchor"
    if "::__residue__::" in symbol or symbol.endswith("::__residue__:: HEAD "):
        return "residue"
    if "__residue__" in symbol:
        return "residue"
    return "edit"


def op_records(repo: Path, op_id: str) -> dict[str, list[str]]:
    """symbol records of one op, bucketed by kind."""
    path = repo / ".sgt" / "ops" / op_id
    out: dict[str, list[str]] = defaultdict(list)
    if not path.exists():
        out["missing"].append(op_id)
        return out
    for symbol in json.load(open(path)).get("footprint", {}):
        out[classify(symbol)].append(symbol)
    return out


STOPWORDS = {"the", "and", "with", "from", "into", "when", "that", "this", "their", "them",
             "then", "each", "over", "your", "have", "been", "were", "will", "than", "onto"}


def label_tokens(label: str) -> list[str]:
    words = "".join(c if c.isalnum() else " " for c in label.lower()).split()
    return [w for w in words if len(w) >= 4 and w not in STOPWORDS]


def match_target(symbol: str) -> str:
    """The part of a symbol a label token may legitimately match: filename + qualname, never the
    package directory. The first version matched the whole path, and in coursecraft the package is
    literally named `coursecraft/`, so the token "course" matched every symbol in the repo and
    `Course Search` scored 0.95 while its isomorphic twin `Conference CLI` scored 0.00. The metric
    was reading the project's name, not the label's fit."""
    return symbol.rsplit("/", 1)[-1].lower()


def label_coverage(label: str, symbols: set[str]) -> tuple[float, list[str]]:
    """Fraction of a feature's symbols whose filename-or-qualname contains a label content token."""
    tokens = label_tokens(label)
    if not tokens or not symbols:
        return (1.0, tokens)          # nothing to disagree with; not a mislabel claim
    hit = [s for s in symbols if any(t in match_target(s) for t in tokens)]
    return (len(hit) / len(symbols), tokens)


def build_identity(repo: Path) -> dict:
    """What was measured, and what measured it -- refusing when they disagree.

    Three times in one day this census reported a decomposition that was not the one under test: a
    re-clustered copy (the analysis tool rebuilt the fixture it was analysing), then the genuine
    fixture built by superseded code, then a rebuild I had asserted was un-credentialed and wasn't.
    None of that broke a stated rule; the rules just never said "check that the artifact you measured
    is the artifact you shipped".

    `setup-study-session.sh` has carried exactly this check for the participant path from the start.
    This is the same guard pointed at the evaluation path instead. It is not a new rule, it is R1
    (frozen system) made checkable: a census whose header cannot name a build sha and a matching
    `signals_version` is a census of nothing in particular."""
    tree = json.loads((repo / ".sgt/tree/tree.json").read_text())["data"]
    built = str(tree.get("signals_version"))
    from sgt.lens.cluster import SIGNALS_VERSION

    installed = str(SIGNALS_VERSION)
    if built != installed:
        raise SystemExit(
            f"refusing to census {repo.name}: its history view was built at signals_version "
            f"{built}, the installed sgt is at {installed}. Every feature would regroup on the next "
            f"refresh, so this tree is not what the installed code produces. Rebuild the fixture."
        )
    src = Path(__file__).resolve().parents[3]
    def git(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=src, capture_output=True, text=True).stdout.strip()
    dirty = git("status", "--porcelain") + git("diff", "HEAD")
    sha = git("rev-parse", "--short", "HEAD")
    if dirty:
        sha += "+" + __import__("hashlib").sha256(dirty.encode()).hexdigest()[:8]
    unlabelled = sorted(fid for fid, nd in tree["nodes"].items()
                        if not nd.get("children") and not nd.get("label"))
    if unlabelled:
        raise SystemExit(
            f"refusing to census {repo.name}: {len(unlabelled)} leaf/leaves carry no label, so `sgt "
            f"show` would print a 64-char id where the name belongs. A rebuild without a credential "
            f"unnames features rather than renaming them; this tree is one of those."
        )
    return {"sgt_build": sha, "dirty": bool(dirty), "signals_version": built,
            "leaf_count": sum(1 for nd in tree["nodes"].values() if not nd.get("children"))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", type=Path)
    ap.add_argument("--episodes", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    repo = args.repo.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    build = build_identity(repo)
    print(f"build {build['sgt_build']}"
          + ("  (DIRTY WORKING TREE — not a frozen system)" if build["dirty"] else "")
          + f"  ·  fixture signals_version {build['signals_version']}"
          f"  ·  {build['leaf_count']} leaves")

    log = sgt_log(repo)
    (out / "sgt-log.json").write_text(json.dumps(log, indent=1))

    episodes = json.loads(args.episodes.read_text())      # sha prefix -> episode name
    label = {fid: f["label"] for fid, f in log["features"].items()}
    idx_sha = {c["index"]: c["sha"] for c in log["commits"]}
    idx_subject = {c["index"]: c["subject"] for c in log["commits"]}
    idx_bk = {c["index"]: c["bookkeeping"] for c in log["commits"]}

    def episode_of(idx: int) -> str:
        sha = idx_sha[idx]
        for prefix, name in episodes.items():
            if sha.startswith(prefix):
                return name
        return f"(unmapped {sha[:7]})"

    # episode -> feature -> {kind: [symbols]}
    per_episode: dict[int, dict[str, dict[str, list[str]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for cell in log["cells"]:
        for op in cell["op_ids"]:
            for kind, symbols in op_records(repo, op).items():
                per_episode[cell["commit_index"]][cell["feature_id"]][kind].extend(symbols)

    rows = []
    for idx in sorted(idx_sha):
        feats = per_episode.get(idx, {})
        substantive = {fid: v for fid, v in feats.items() if v.get("edit")}
        rows.append({
            "commit_index": idx,
            "sha": idx_sha[idx][:7],
            "episode": episode_of(idx),
            "subject": idx_subject[idx],
            "bookkeeping": idx_bk[idx],
            "features_substantive": {
                label[fid]: sorted(v["edit"]) for fid, v in sorted(substantive.items(), key=lambda kv: -len(kv[1]["edit"]))
            },
            "features_bookkeeping_only": sorted(
                label[fid] for fid, v in feats.items() if not v.get("edit")
            ),
            "n_features_substantive": len(substantive),
            "n_edit_records": sum(len(v["edit"]) for v in substantive.values()),
            "n_anchor_records": sum(len(v.get("anchor", [])) for v in feats.values()),
            "n_residue_records": sum(len(v.get("residue", [])) for v in feats.values()),
        })

    # feature-side view: which episodes each feature spans, and which symbols it holds
    spans: dict[str, list[str]] = defaultdict(list)
    feature_symbols: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for lab, syms in row["features_substantive"].items():
            spans[lab].append(row["episode"])
            feature_symbols[lab].update(syms)

    flags = []
    for row in rows:
        if row["bookkeeping"]:
            continue
        if row["n_features_substantive"] == 0:
            flags.append({"episode": row["episode"], "flag": "miss",
                          "detail": "no substantive ops filed for this commit"})
        elif row["n_features_substantive"] > 2:
            flags.append({"episode": row["episode"], "flag": "split",
                          "detail": f"{row['n_features_substantive']} features hold this episode's edits: "
                                    + ", ".join(row["features_substantive"])})
    for lab, eps in sorted(spans.items()):
        distinct = sorted(set(e for e in eps if not e.startswith("(")))
        if len(distinct) > 3:
            flags.append({"feature": lab, "flag": "lump",
                          "detail": f"spans {len(distinct)} episodes: " + ", ".join(distinct)})

    coverage = {}
    for lab, syms in sorted(feature_symbols.items()):
        frac, tokens = label_coverage(lab, syms)
        coverage[lab] = {"coverage": round(frac, 3), "tokens": tokens, "n_symbols": len(syms)}
        if len(syms) >= 5 and frac < 0.34:
            off = sorted(s for s in syms if not any(t in match_target(s) for t in tokens))
            flags.append({"feature": lab, "flag": "mislabel",
                          "detail": f"label covers {frac:.0%} of its {len(syms)} symbols; "
                                    f"{len(off)} unrelated, e.g. " + ", ".join(off[:4])})
    covers_all = sorted(lab for lab, c in coverage.items()
                        if c["coverage"] == 1.0 and c["n_symbols"] >= 5)
    if covers_all:
        flags.append({"flag": "label-covers-all",
                      "detail": f"{len(covers_all)} feature(s) whose label covers every symbol: "
                                + ", ".join(covers_all)})

    # Token-*subset* duplicates, not equal token sets: "Course Search" and "add course search"
    # are the pair a reader confuses, and an equal-set test misses it.
    label_list = sorted(spans)
    seen_pairs = set()
    for a in label_list:
        for b in label_list:
            if a >= b:
                continue
            ta, tb = set(label_tokens(a)), set(label_tokens(b))
            if ta and tb and (ta <= tb or tb <= ta) and (a, b) not in seen_pairs:
                seen_pairs.add((a, b))
                flags.append({"flag": "near-duplicate-label",
                              "detail": f"{a!r} ({len(feature_symbols[a])} symbols) / "
                                        f"{b!r} ({len(feature_symbols[b])} symbols)"})
    path_labels = sorted(lab for lab in spans if lab.endswith((".py", ".ini", ".md", ".txt")))
    if path_labels:
        flags.append({"flag": "file-path-label", "detail": ", ".join(path_labels)})

    # A commit the episode map does not name is a hole in the *ground truth*, not in sgt -- and it is
    # invisible where it appears, because `(unmapped a58003c)` sits in a 28-row table reading like a
    # row rather than like a problem. Coursecraft's map missed `a58003c` (the first build of the
    # capacity episode, before the designed `sgt undo` and the redo) for a full day of analysis: its
    # feature spans were counted one episode short and its row was attributed to nothing. Counted
    # here, next to the flag total, which is where I actually look. Kept out of `flags` deliberately --
    # these are defects in the record, and mixing them into the count would make sgt look worse
    # whenever the map is incomplete.
    unmapped = [r["sha"] for r in rows if r["episode"].startswith("(unmapped")]

    report = {
        "repo": str(repo),
        "build": build,
        "unmapped_commits": unmapped,
        "counts": {k: log[k] for k in
                   ("commit_count", "save_count", "bookkeeping_count", "op_count", "feature_count")},
        "episodes_mapped": sum(1 for r in rows if not r["episode"].startswith("(")),
        "rows": rows,
        "feature_spans": {lab: sorted(set(eps)) for lab, eps in sorted(spans.items())},
        "label_coverage": coverage,
        "flags": flags,
    }
    (out / "census.json").write_text(json.dumps(report, indent=1))

    print(f"{repo.name}: {report['counts']}")
    print(f"{'episode':<22} {'sha':<8} {'feat':>4} {'edit':>5} {'anch':>5} {'res':>5}  features")
    for row in rows:
        bk = " (bookkeeping)" if row["bookkeeping"] else ""
        print(f"{row['episode']:<22} {row['sha']:<8} {row['n_features_substantive']:>4} "
              f"{row['n_edit_records']:>5} {row['n_anchor_records']:>5} {row['n_residue_records']:>5}  "
              f"{', '.join(row['features_substantive']) or '-'}{bk}")
    print()
    for f in flags:
        print(f"[{f['flag']}] {f.get('episode') or f.get('feature') or ''} — {f['detail']}")
    if unmapped:
        print(f"\n⚠ {len(unmapped)} of {len(rows)} commits are not in the episode map "
              f"({', '.join(unmapped)}) — their rows are attributed to no episode and do not count "
              f"toward any feature's span. Complete the map before reading the numbers below.")
    print(f"\n{len(flags)} flags. Full table: {out/'census.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
