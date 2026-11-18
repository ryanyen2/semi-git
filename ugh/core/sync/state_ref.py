"""`refs/ugh/state`: the git ref that carries what history cannot give, the names table and the
captured prompts. Ops never travel; every clone mines them from history.

The ref is a parent-chained series of commits whose tree holds `.sgt/names.json` and
`.sgt/intent/prompts.json`, fetched and pushed beside the branches. The `.sgt/` files on disk are
the working copy; `publish_from_local` writes them onto the ref and `merge_remote` folds another
clone's tip into them. Everything under `.sgt/local/` stays on this machine.
"""

from __future__ import annotations

from pathlib import Path

from ugh import names, state
from ugh.store.gitbind import GitBinding

STATE_REF = "refs/ugh/state"

# A scratch ref a fetch force-updates to the remote's state tip, leaving `STATE_REF` alone.
FETCH_STATE_REF = "refs/ugh/remote-state"

_PUBLISH_RETRIES = 5

_TRAVELING_TABLES: tuple[str, ...] = ("names", "intent_prompts")


class StateRefError(Exception):
    """A state-ref advance could not be made durable (final CAS failure)."""


def read_sha(gb: GitBinding) -> str | None:
    """The `refs/ugh/state` tip, or None before the first publish."""
    return gb.rev_parse(STATE_REF)


def read_tree(gb: GitBinding, sha: str | None = None) -> dict[str, bytes]:
    """Every `.sgt/**` blob carried at `sha` (the state-ref tip when omitted), path -> bytes."""
    tip = sha if sha is not None else read_sha(gb)
    return gb.read_tree_blobs(tip) if tip is not None else {}


def _local_blobs(repo: Path) -> dict[str, bytes]:
    """The traveling tables as they sit in this clone's `.sgt/`, byte for byte."""
    blobs: dict[str, bytes] = {}
    for name in _TRAVELING_TABLES:
        p = state.path(repo, name)
        if p.is_file():
            blobs[state.rel(name)] = p.read_bytes()
    return blobs


def publish_from_local(gb: GitBinding, repo: Path, merged: str | None = None) -> str | None:
    """Write this clone's traveling tables onto `refs/ugh/state` and return the tip. `merged` is a
    state commit just folded in; it becomes a second parent so the next merge base sees it. Raises
    `StateRefError` when the ref cannot be advanced."""
    blobs = _local_blobs(repo)
    for _ in range(_PUBLISH_RETRIES):
        tip = gb.rev_parse(STATE_REF)
        new_tree = gb.write_tree_from_blobs(blobs)
        extra = [merged] if merged and not (tip and gb.is_ancestor(merged, tip)) else []
        if not extra and tip is not None and gb.rev_parse(f"{tip}^{{tree}}") == new_tree:
            return tip
        commit = gb.commit_tree(new_tree, ([tip] if tip else []) + extra, "ugh state")
        if gb.update_ref_cas(STATE_REF, commit, tip):
            return commit
    raise StateRefError(f"could not advance {STATE_REF} after {_PUBLISH_RETRIES} attempts")


def merge_remote(gb: GitBinding, repo: Path, theirs: str | None) -> list[tuple[str, str, str]]:
    """Fold another clone's state commit `theirs` into this one: names three-way per op against
    the merge base of the two state tips, prompts unioned by key, then publish with `theirs` as a
    second parent. Returns `(op, ours, theirs)` for each op both sides labelled differently (ours
    is kept)."""
    from ugh.core.store import locked_section
    from ugh.intent import prompts

    ours = read_sha(gb)
    if theirs is None or (ours and gb.is_ancestor(theirs, ours)):
        return []
    base = gb.merge_base(ours, theirs) if ours else None
    table, conflicts = names.merge(names.at(gb, base), names.load(repo), names.at(gb, theirs))
    with locked_section(repo):
        names.save(repo, table)
        state.save_json(repo, "intent_prompts",
                        prompts.merge(prompts.load_prompts(repo), prompts.prompts_at(gb, theirs)))
    publish_from_local(gb, repo, merged=theirs)
    return conflicts


def publish_and_push(gb: GitBinding, repo: Path, remote: str, retries: int = _PUBLISH_RETRIES) -> str:
    """Publish this clone's tables onto `refs/ugh/state` and push the ref to `remote`. A rejected
    push (the remote moved) fetches the remote tip, merges it in and pushes again. Returns the
    pushed tip; raises `StateRefError` when it cannot succeed within `retries`."""
    publish_from_local(gb, repo)
    for _ in range(retries):
        if gb.push_ref(remote, f"{STATE_REF}:{STATE_REF}"):
            tip = gb.rev_parse(STATE_REF)
            if tip is None:
                raise StateRefError(f"pushed {STATE_REF} to {remote} but the local ref is unresolved")
            return tip
        if gb.fetch_ref(remote, f"+{STATE_REF}:{FETCH_STATE_REF}"):
            merge_remote(gb, repo, gb.rev_parse(FETCH_STATE_REF))
    raise StateRefError(f"could not publish {STATE_REF} to {remote} after {retries} attempts")
