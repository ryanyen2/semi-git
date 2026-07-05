"""Mine an entity-level patch stream from git history.

This is the one primitive sgt does not ship yet: intersect each commit's changed
line-ranges (``GitBinding.diff_name_and_text``) against entity spans
(``entities.extract_file``) to learn *which functions/classes each commit actually
touched* — not which files, not which lines. Everything else here reuses sgt.

Output is an ordered patch stream (oldest -> newest) plus a stable-identity map. Identity
is resolved by a tiered content matcher ported from sem (``identity_match``): entities link
across renames and moves by body hash / fuzzy token overlap — within a file, across a file
rename, and across files — so a renamed or moved function keeps one id across the timeline.
See ``identity_churn`` for how many links each tier found.

Run standalone to validate mining before any clustering/LLM:
    .venv/bin/python experiments/patch_clustering/mine.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sgt.entities.extract import extract_file  # noqa: E402
from sgt.store.gitbind import GitBinding  # noqa: E402

from experiments.patch_clustering.identity_match import (  # noqa: E402
    Snap,
    detect_splits_merges,
    link_residual,
    match_pair,
    snapshot,
)

_OUT = Path(__file__).resolve().parent / "out"


@dataclass(frozen=True)
class EntityPatch:
    """One entity's change at one commit. ``surface_id`` is ``file::name`` as seen at this
    commit; ``entity_id`` is the rename-stable canonical id (resolved in a second pass)."""

    order: int  # 0-based position oldest -> newest
    sha: str
    short_sha: str
    subject: str
    surface_id: str
    entity_id: str  # filled after union-find resolves renames
    name: str
    file: str
    kind: str  # function | class | method
    change: str  # added | modified | removed


@dataclass
class MineResult:
    patches: list[EntityPatch]
    commits: list[dict]  # {order, sha, short, subject}
    change_sets: dict[int, list[str]]  # commit order -> canonical entity ids touched
    identity_churn: dict
    lifecycle: list[dict]  # split/merge/death events (canonical ids), ordered by commit


class _UnionFind:
    """Links surface ids across renames so a moved entity resolves to one canonical root."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _history(repo: Path) -> list[tuple[str, str | None, str]]:
    """(sha, first_parent, subject) oldest-first. First-parent diffs keep merges from
    re-attributing a whole side branch onto the merge commit (a v1 simplification)."""
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--reverse", "--format=%H%x1f%P%x1f%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    rows: list[tuple[str, str | None, str]] = []
    for line in out.splitlines():
        if not line:
            continue
        sha, parents, subject = line.split("\x1f", 2)
        first_parent = parents.split()[0] if parents.strip() else None
        rows.append((sha, first_parent, subject))
    return rows


def mine(repo: Path) -> MineResult:
    gb = GitBinding(repo)
    uf = _UnionFind()
    raw: list[EntityPatch] = []
    commits: list[dict] = []

    link_counts = {"renamed_within_file": 0, "moved_file_rename": 0, "moved_cross_file": 0}
    raw_lifecycle: list[dict] = []  # split/merge, surface ids — resolved to canonical below

    for order, (sha, parent, subject) in enumerate(_history(repo)):
        commits.append({"order": order, "sha": sha, "short": sha[:8], "subject": subject})

        def emit(snap: Snap, change: str) -> None:
            surface = snap.ent.id  # f"{file}::{name}" as seen at this commit
            uf.add(surface)
            raw.append(
                EntityPatch(
                    order=order,
                    sha=sha,
                    short_sha=sha[:8],
                    subject=subject,
                    surface_id=surface,
                    entity_id=surface,  # resolved in the second pass
                    name=snap.ent.name,
                    file=snap.ent.file,
                    kind=snap.ent.kind,
                    change=change,
                )
            )

        commit_added: list[Snap] = []
        commit_removed: list[Snap] = []

        for fc in gb.diff_name_and_text(parent, sha):
            new_src = gb.file_at(sha, fc.path)
            new_entities = extract_file(fc.path, new_src) if new_src else []
            old_ref = fc.old_path or fc.path
            old_src = gb.file_at(parent, old_ref) if parent else None
            old_entities = extract_file(old_ref, old_src) if old_src else []

            m = match_pair(
                snapshot(old_entities, old_src or ""),
                snapshot(new_entities, new_src or ""),
            )
            for s in m.modified:
                emit(s, "modified")
            for old, new in m.links:  # rename within a file, or move across a file rename
                uf.union(old.ent.id, new.ent.id)
                key = "renamed_within_file" if old.ent.file == new.ent.file else "moved_file_rename"
                link_counts[key] += 1
                emit(new, "modified")
            commit_added.extend(m.added)
            commit_removed.extend(m.removed)

        # Cross-file moves: a function cut from one file and pasted into another shows up as
        # a leftover removal + a leftover addition in the same commit — link them by body.
        cross_links, matched_r, matched_a = link_residual(commit_removed, commit_added)
        for old, new in cross_links:
            uf.union(old.ent.id, new.ent.id)
            link_counts["moved_cross_file"] += 1
            emit(new, "modified")
        res_added = [s for s in commit_added if s.ent.id not in matched_a]
        res_removed = [s for s in commit_removed if s.ent.id not in matched_r]
        for s in res_added:
            emit(s, "added")
        for s in res_removed:
            emit(s, "removed")

        # 1->many / many->1 reshapes among what's left: a function split into several, or several
        # merged into one. Records the relationship; the entities are still emitted add/removed above.
        splits, merges = detect_splits_merges(res_removed, res_added)
        for sp in splits:
            raw_lifecycle.append({"order": order, "type": "split", **sp})
        for mg in merges:
            raw_lifecycle.append({"order": order, "type": "merge", **mg})

    # Second pass: resolve rename-stable canonical ids now that all unions are known.
    patches = [
        EntityPatch(**{**asdict(p), "entity_id": uf.find(p.surface_id)}) for p in raw
    ]
    change_sets = {}
    for p in patches:
        change_sets.setdefault(p.order, set()).add(p.entity_id)

    surfaces = {p.surface_id for p in patches}
    canon = {uf.find(s) for s in surfaces}
    churn = {
        **link_counts,
        "distinct_surfaces": len(surfaces),
        "distinct_canonical": len(canon),
        "renames_linked": len(surfaces) - len(canon),
    }

    # Resolve split/merge ids to canonical, then derive death events: an entity whose last event in
    # the whole stream is a removal is permanently dead (rebirth would append a later `added`).
    lifecycle: list[dict] = []
    for e in raw_lifecycle:
        if e["type"] == "split":
            lifecycle.append({**e, "from": uf.find(e["from"]), "to": [uf.find(t) for t in e["to"]]})
        else:
            lifecycle.append({**e, "from": [uf.find(f) for f in e["from"]], "to": uf.find(e["to"])})
    last_event: dict[str, tuple[int, str]] = {}
    for p in patches:
        prev = last_event.get(p.entity_id)
        if prev is None or p.order >= prev[0]:
            last_event[p.entity_id] = (p.order, p.change)
    for eid, (order, change) in last_event.items():
        if change == "removed":
            lifecycle.append({"order": order, "type": "death", "entity": eid})
    lifecycle.sort(key=lambda e: (e["order"], e["type"]))

    return MineResult(
        patches=patches,
        commits=commits,
        change_sets={k: sorted(v) for k, v in change_sets.items()},
        identity_churn=churn,
        lifecycle=lifecycle,
    )


def _save(result: MineResult) -> Path:
    _OUT.mkdir(parents=True, exist_ok=True)
    path = _OUT / "patches.json"
    path.write_text(
        json.dumps(
            {
                "patches": [asdict(p) for p in result.patches],
                "commits": result.commits,
                "change_sets": result.change_sets,
                "identity_churn": result.identity_churn,
                "lifecycle": result.lifecycle,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _summary(result: MineResult) -> None:
    from collections import Counter

    changes = Counter(p.change for p in result.patches)
    per_entity = Counter(p.entity_id for p in result.patches)
    print(f"commits:           {len(result.commits)}")
    print(f"entity-patches:    {len(result.patches)}  {dict(changes)}")
    print(f"distinct entities: {result.identity_churn['distinct_canonical']} "
          f"(surfaces {result.identity_churn['distinct_surfaces']}, "
          f"renames linked {result.identity_churn['renames_linked']})")
    lc = Counter(e["type"] for e in result.lifecycle)
    print(f"lifecycle events:  {dict(lc)}")
    print("\ntop 12 most-changed entities:")
    for eid, n in per_entity.most_common(12):
        print(f"  {n:2d}  {eid}")
    print("\nfirst 5 commits (entities touched):")
    for c in result.commits[:5]:
        ids = result.change_sets.get(c["order"], [])
        print(f"  [{c['order']:2d}] {c['short']} {c['subject'][:48]:48s} -> {len(ids)} entities")


if __name__ == "__main__":
    result = mine(_REPO_ROOT)
    _summary(result)
    out = _save(result)
    print(f"\nwrote {out}")
