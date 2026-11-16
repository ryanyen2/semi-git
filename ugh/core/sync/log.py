"""D1: an append-only, ugh-native record of every shared-branch `land`.

A dedicated git ref per branch (`refs/ugh/log/<branch>`) holds a parent-chained series of
empty-tree commits, one per successful `land`, each carrying the landed commit's sha and the
ideal it produced as trailers. It gives base recovery (`ingest.recover_base`/`_theirs_ideal`) an
authoritative rung-0 lookup for "what ideal did commit X land with" -- no trailers to lose in a
squash, no `.sgt/ideal.json` to go stale -- and gives `land` a pre-flight signal for D6 (someone
landed since our last sync point).

The log only records `land` -- the authoritative shared-branch advance. A local `sync` merge
advances only the local branch and carries its own `Sgt-Op:` trailers, so it needs no entry; any
merge-base on a shared branch was itself a landed commit and is therefore already in the log. This
module is best-effort throughout: a failure to append or read never blocks `land` or `sync`, it
just means base recovery falls through to the existing trailers/mine ladder.
"""

from __future__ import annotations

from dataclasses import dataclass

from ugh.store.gitbind import (
    GitBinding,
    parse_landed_sha,
    parse_op_ids,
)

LOG_REF_PREFIX = "refs/ugh/log/"


def log_ref(branch: str) -> str:
    return f"{LOG_REF_PREFIX}{branch}"


@dataclass(frozen=True)
class LogEntry:
    landed_sha: str
    ideal_ids: frozenset[str]


def read(gb: GitBinding, branch: str) -> list[LogEntry]:
    """Every entry for `branch`, newest first. Empty if the log ref doesn't exist or a read fails
    -- the same best-effort discipline as `append`, since a missing/corrupt log is just an absent
    rung-0, not an error."""
    try:
        entries = []
        for sha in gb.commit_shas(log_ref(branch)):
            message = gb.commit_message(sha)
            landed_sha = parse_landed_sha(message)
            if landed_sha is None:
                continue  # not one of ours (shouldn't happen on our own ref, but don't trust blindly)
            entries.append(LogEntry(landed_sha=landed_sha, ideal_ids=frozenset(parse_op_ids(message))))
        return entries
    except Exception:
        return []


def ideal_for_sha(gb: GitBinding, branch: str, sha: str) -> frozenset[str] | None:
    """The ideal the log records `sha` having landed with, or None if `sha` isn't in the log."""
    for entry in read(gb, branch):
        if entry.landed_sha == sha:
            return entry.ideal_ids
    return None
