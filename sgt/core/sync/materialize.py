"""Sync stage 4 -- persist the reconciled union and land the merge commit (plan U19, D4).

Only reached fork-free. This is the one stage that writes: persist theirs' op files for real
(unioning provenance a same-id collision would otherwise drop, R8), write the reconciled
pins/declared/tree, fold the source from the union, then commit a real 2-parent merge whose tree is
*exactly* what sgt wrote -- no textual 3-way merge runs on any path. The second parent is joined by
writing `.git/MERGE_HEAD` ourselves (`gb.complete_merge`), replacing the old `git merge -X ours`;
the two-clone tests assert the resulting tree is independently `code(merged_ideal, all_ops)`.

`land` (U23) reuses this stage over a locally-sourced union, so it takes the resolved state
explicitly rather than recomputing anything from disk.
"""

from __future__ import annotations

from pathlib import Path

from sgt.core import lens
from sgt.core.fold import code
from sgt.core.store import Store
from sgt.lens import tree
from sgt.lens.pins import save_pins
from sgt.store.gitbind import GitBinding, format_op_trailers

from .ingest import Ingested
from .resolve import Resolution


def materialize(
    repo: Path,
    gb: GitBinding,
    remote: str,
    branch: str,
    theirs_sha: str,
    ing: Ingested,
    res: Resolution,
) -> str:
    store = Store(repo)
    store.init()
    for op in [*ing.theirs_ops, *ing.mined_ops]:
        store.add(op)  # re-unions provenance a same-id collision would otherwise drop (R8); the
        # mined foreign commits (C3) have no op file anywhere else, so they land for real here

    save_pins(repo, res.unioned_pins)
    lens._save_declared(repo, res.declared)
    tree.save(repo, res.tree_result)

    materialized = code(res.merged_ideal, ing.all_ops)
    lens._write_working_tree(repo, materialized)

    trailers = format_op_trailers(sorted(res.merged_ideal.op_ids))
    merge_sha = gb.complete_merge(f"sgt sync: merge {remote}/{branch}", theirs_sha, trailers=trailers)
    lens.record_ideal(repo, res.merged_ideal, merge_sha)
    return merge_sha
