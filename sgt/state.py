"""One owner for the `.sgt/` on-disk layout and its JSON schema envelope (plan U17, D3/C2).

Before this module, `.sgt/` layout knowledge was scattered across a dozen modules, each
hand-rolling its own path and `json.loads`/`write_text`, with no schema versioning. That is a
problem specific to sgt: `sync` reads committed artifacts (pins, declared, tree) not just from the
working tree but from *arbitrary historical git blobs* -- a teammate's ref tip can be any vintage
(`gb.blob_bytes(theirs_sha, ...)`). So an on-disk format is never retired from the read path; once
a new schema ships, every reader must keep parsing every schema any sgt version ever committed,
forever. Designing that in now -- before the five collaboration artifacts (committed `ideal.json`,
`forks.json`, claims, proposals, aliases; U20-U24) exist -- is cheap; retrofitting it later is not.

Every JSON artifact's payload is wrapped in a uniform envelope, `{"schema": <int>, "data": <body>}`.
A payload lacking that envelope is implicitly v0 (the pre-U17 shape, byte-for-byte what is committed
in every real repo's history today). `load_*` accept v0 *and* v1 (dispatch on the envelope); `dump`/
`save_json` emit the current version. The same decoder serves a working-tree read and a
blob-at-SHA read, so the historical-blob dispatch that `sync`'s `_pins_at`/`_declared_at` did ad hoc
per artifact now lives in exactly one place (`load_blob_json`).

Only the small JSON tables route through here. The content-addressed op store (`.sgt/ops/`,
`.sgt/local/hollow/`) keeps its own atomic-write + fsck codec in `sgt.core.store` -- it is not a
"small JSON table" and its content-addressing already gives it cross-version stability.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

SGT_DIR = ".sgt"


def _atomic_write_text(p: Path, text: str) -> None:
    """Durable write (R5): a temp file in the same directory, fsync'd, then atomically renamed
    over the target. A reader sees the old bytes or the new bytes, never a torn file, and a crash
    mid-write leaves the prior file intact -- the property every `.sgt` metadata write now has."""
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)  # atomic rename on POSIX
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

# The envelope version every writer emits. Bump only when an artifact's *content* shape changes;
# readers keep accepting every prior version (D3 -- historical blobs are read forever).
SCHEMA = 1


@dataclass(frozen=True)
class _Artifact:
    """A `.sgt/` JSON artifact's layout and serialization convention. `committed` records whether
    it travels in git (and is therefore read from historical blobs -- the artifacts whose schema
    dispatch actually bites); `sort_keys`/`newline` reproduce each file's existing byte format so
    routing it through the shared codec changes nothing on disk."""

    parts: tuple[str, ...]
    committed: bool
    sort_keys: bool = True
    newline: bool = True


# The registry: logical name -> layout. New collaboration artifacts (U20-U24: committed
# `ideal.json`, `forks.json`, `claims/`, `proposals/`, `aliases`) get their slot added here, all
# `committed=True`, and inherit the envelope + blob dispatch for free.
_ARTIFACTS: dict[str, _Artifact] = {
    # committed, team-shared -- read from arbitrary-vintage historical blobs by `sync`.
    "oracle_config": _Artifact(("oracle.json",), committed=True),
    "identity_constraints": _Artifact(("identity_constraints.json",), committed=True, sort_keys=False),
    "declared": _Artifact(("declared.json",), committed=True, sort_keys=False),
    "pins": _Artifact(("pins", "pins.json"), committed=True, sort_keys=False),
    "tree": _Artifact(("tree", "tree.json"), committed=True),
    # committed recovery record of a witness commit's own ideal (op-id list) -- survives a
    # squash/rebase that destroys `Sgt-Op:` trailers (C5). Distinct from the local, per-ref
    # `ideal_table` below (that stays authoritative for the *current* ref; this is in-tree history).
    "ideal": _Artifact(("ideal.json",), committed=True),
    # committed record of the open same-symbol forks a sync surfaced (C4) -- a fork is durable,
    # shared state that travels with the repo so a teammate's next sync (and `sgt status`) sees it.
    "forks": _Artifact(("forks.json",), committed=True),
    # committed G-Set of feature-id aliases: old-id -> new-id from the birth-id migration (U21/D6).
    # Additive-only so a stale reference from an un-migrated clone's history still resolves after
    # that clone syncs; a same-old collision (divergent unsynced curation) resolves by the alias-
    # merge rule. Travels with the repo and is read from historical blobs, like every committed slot.
    "aliases": _Artifact(("aliases.json",), committed=True),
    # committed OR-Set of declared order edges (`sgt after`/`sgt after --retract`, U21/D6): adds
    # carry a unique tag, retraction tombstones observed tags, live = adds minus tombstoned. A new
    # path (the legacy flat G-Set stays at `declared` in v0 shape for old readers, D3 old-reader
    # policy); `sgt.core.lens` resolves this down to the plain live edge set every consumer expects.
    "declared_orset": _Artifact(("declared_edges.json",), committed=True),
    # committed three-tier file-boundary overrides (`sgt tiers set`, U27/D4): explicit
    # entity/opaque/ignored patterns, the escape hatch over the built-in grammar-presence
    # default. Read from historical blobs at mining time (`sgt.core.tiers.load_tiers_at`) so
    # tier assignment stays a pure function of the mined commit (LAW-0).
    "tiers": _Artifact(("tiers.json",), committed=True),
    # local, gitignored -- per-clone, never travels, never read from a blob.
    "verdicts": _Artifact(("local", "oracle.json"), committed=False),
    "witness": _Artifact(("local", "witness.json"), committed=False),
    "ideal_table": _Artifact(("local", "ideal.json"), committed=False),
    # local, gitignored per-ref stack of prior committed ideals: `record_ideal` pushes the outgoing
    # ideal (+ its witness) before each overwrite, so `sgt undo` (U26) can restore the ideal a
    # revert/restore/rewrite/save last replaced. Never travels; there is no edit history to invert
    # until this log exists, which is exactly why U26 must add it.
    "ideal_journal": _Artifact(("local", "ideal_journal.json"), committed=False),
    "drafts": _Artifact(("local", "drafts.json"), committed=False),
    "staged": _Artifact(("local", "staged.json"), committed=False),
    "label_cache": _Artifact(("local", "label_cache.json"), committed=False, sort_keys=False, newline=False),
    "plan_sessions": _Artifact(("local", "plan_sessions.json"), committed=False),
    "plan_matches": _Artifact(("local", "plan_matches.json"), committed=False),
    # local, gitignored record of scratch-tree sessions (`sgt session start`, U30/D5): name ->
    # branch/scratch path/target branch/base op-ids/owning pid/start time. Per-clone, never
    # travels -- a session's scratch tree is a `git worktree` of *this* clone's object store.
    "sessions": _Artifact(("local", "sessions.json"), committed=False),
    # local, gitignored transactional-land journal (`sgt land`, U5/R7): `{ref, snapshot}` written
    # before the candidate tree is materialized and cleared on every landing/non-landing exit. A
    # crash mid-land leaves it behind, so the next `land` finds it and rolls the working tree back
    # to `snapshot`; `fsck` names the interrupted state. Per-clone, never travels.
    "land_pending": _Artifact(("local", "land_pending.json"), committed=False),
}


# -- directory layout -----------------------------------------------------------------------------

def sgt_dir(repo: str | Path) -> Path:
    return Path(repo) / SGT_DIR


def subdir(repo: str | Path, *parts: str) -> Path:
    return Path(repo).joinpath(SGT_DIR, *parts)


# -- artifact paths -------------------------------------------------------------------------------

def path(repo: str | Path, name: str) -> Path:
    """The absolute path of artifact `name` under this repo's `.sgt/`."""
    return Path(repo).joinpath(SGT_DIR, *_ARTIFACTS[name].parts)


def rel(name: str) -> str:
    """The artifact's repo-relative path (`.sgt/...`) -- the key a blob read uses (`gb.blob_bytes`,
    `gb.list_tree`)."""
    return "/".join((SGT_DIR, *_ARTIFACTS[name].parts))


# -- schema envelope ------------------------------------------------------------------------------

def _unwrap(payload):
    """Return an artifact's logical body, dispatching on the envelope: a `{"schema": <int>, "data":
    ...}` wrapper is v1 (or later); anything else is v0 -- the pre-U17 shape, where the parsed JSON
    *is* the body. Every real repo's committed history today is v0, and blob reads of it must keep
    working forever (D3)."""
    if isinstance(payload, dict) and isinstance(payload.get("schema"), int) and "data" in payload:
        return payload["data"]
    return payload


def _encode(body, art: _Artifact) -> str:
    envelope = {"schema": SCHEMA, "data": body}
    text = json.dumps(envelope, indent=2, sort_keys=art.sort_keys)
    return text + "\n" if art.newline else text


def load_json(repo: str | Path, name: str, default=None):
    """The logical body of artifact `name` from the working tree, or `default` if absent. Accepts
    both v0 (no envelope) and v1 payloads. A torn file (a crash mid-write before atomic writes
    shipped, R6) degrades to `default` for a *local* reseedable artifact -- the next verb re-mines
    it -- but re-raises for a *committed* artifact, whose corruption is real and must reach `fsck`
    loudly rather than being silently reseeded into a wrong shared state."""
    p = path(repo, name)
    if not p.is_file():
        return default
    try:
        return _unwrap(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError):
        if _ARTIFACTS[name].committed:
            raise
        return default


def decode_blob_json(raw: bytes | None, default=None):
    """`load_blob_json`'s decode step, given an already-fetched blob (or None) -- for a caller
    that batched its own `blob_bytes_many` read instead of one `blob_bytes` call per artifact."""
    if raw is None:
        return default
    return _unwrap(json.loads(raw.decode("utf-8")))


def load_blob_json(gb, sha: str, name: str, default=None):
    """The logical body of artifact `name` as committed at `sha` (via any `GitBinding`-shaped `gb`),
    or `default` if absent -- the historical-blob read path, running the same version dispatch as a
    working-tree read. This is the one place `sync` reads a teammate's arbitrary-vintage metadata."""
    raw = gb.blob_bytes(sha, rel(name))
    if raw is None:
        return default
    return _unwrap(json.loads(raw.decode("utf-8")))


def save_json(repo: str | Path, name: str, body) -> None:
    """Write artifact `name`'s `body` to the working tree, in its registered byte format."""
    art = _ARTIFACTS[name]
    _atomic_write_text(path(repo, name), _encode(body, art))


# -- committed claims directory (D8) -------------------------------------------------------------
# Published oracle verdicts (`sgt oracle publish`) live one immutable file per (ideal_key, runner)
# under `.sgt/claims/`, so sync's union is a trivial file-level G-Set: copy any file you don't have
# (no field merge, no re-encode). Unlike the flat JSON tables above, the file *set* is the artifact,
# so these get directory helpers rather than a registry slot -- but each file reuses the same schema
# envelope (`_encode`/`_unwrap`) and byte format (`sort_keys=True`, trailing newline) as every other
# committed single-file artifact. Distinct from the local, per-clone verdict cache, which never travels.
_CLAIM_ART = _Artifact(("claims",), committed=True)


def claims_dir(repo: str | Path) -> Path:
    return subdir(repo, "claims")


def claim_rel(name: str) -> str:
    """The repo-relative path (`.sgt/claims/<name>`) of one claim file -- the key a blob read uses."""
    return "/".join((SGT_DIR, "claims", name))


def save_claim(repo: str | Path, name: str, body) -> None:
    """Write claim file `name` (a full basename like `<ideal_key>.<runner_fp>.json`) to the working
    tree. Claim files are immutable once published; a re-publish by the same runner overwrites the
    identical key, which is a no-op on content."""
    _atomic_write_text(claims_dir(repo) / name, _encode(body, _CLAIM_ART))


def load_claim(repo: str | Path, name: str, default=None):
    """The logical body of claim file `name` from the working tree, or `default` if absent."""
    p = claims_dir(repo) / name
    if not p.is_file():
        return default
    return _unwrap(json.loads(p.read_text(encoding="utf-8")))


def list_claim_files(repo: str | Path) -> list[str]:
    """Sorted basenames of every claim file present in the working tree (empty if none)."""
    d = claims_dir(repo)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file())


def load_blob_claim(gb, sha: str, name: str, default=None):
    """The logical body of claim file `name` as committed at `sha` (the historical-blob read path),
    or `default` if absent -- the same version dispatch as a working-tree read."""
    raw = gb.blob_bytes(sha, claim_rel(name))
    if raw is None:
        return default
    return _unwrap(json.loads(raw.decode("utf-8")))


# -- committed proposals directory (C10) ---------------------------------------------------------
# A proposal (`sgt propose create`) is a committed, immutable review object -- base frontier + Δ
# op-set + feature delta + claim link + provenance -- living one file per id under `.sgt/proposals/`,
# content-addressed by base+Δ. Like claims (D8), the file *set* is the artifact, so sync's union is a
# trivial file-level G-Set (`materialize._union_proposals`): copy any file you don't have, no field
# merge. Each file reuses the same schema envelope and byte format as every other committed
# single-file artifact, so a teammate's proposal arrives verbatim on `sgt sync`.
_PROPOSAL_ART = _Artifact(("proposals",), committed=True)


def proposals_dir(repo: str | Path) -> Path:
    return subdir(repo, "proposals")


def proposal_rel(name: str) -> str:
    """The repo-relative path (`.sgt/proposals/<name>`) of one proposal file -- a blob read's key."""
    return "/".join((SGT_DIR, "proposals", name))


def save_proposal(repo: str | Path, name: str, body) -> None:
    """Write proposal file `name` (a full basename like `<proposal_id>.json`) to the working tree.
    Proposals are immutable once created; a re-create with the same base+Δ overwrites the identical
    content-addressed key, a no-op on content."""
    _atomic_write_text(proposals_dir(repo) / name, _encode(body, _PROPOSAL_ART))


def load_proposal(repo: str | Path, name: str, default=None):
    """The logical body of proposal file `name` from the working tree, or `default` if absent."""
    p = proposals_dir(repo) / name
    if not p.is_file():
        return default
    return _unwrap(json.loads(p.read_text(encoding="utf-8")))


def list_proposal_files(repo: str | Path) -> list[str]:
    """Sorted basenames of every proposal file present in the working tree (empty if none)."""
    d = proposals_dir(repo)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file())


def load_blob_proposal(gb, sha: str, name: str, default=None):
    """The logical body of proposal file `name` as committed at `sha` (the historical-blob read
    path), or `default` if absent -- the same version dispatch as a working-tree read."""
    raw = gb.blob_bytes(sha, proposal_rel(name))
    if raw is None:
        return default
    return _unwrap(json.loads(raw.decode("utf-8")))


# -- committed reviews directory (plan U31, S7) --------------------------------------------------
# A review record (`sgt review-queue ack`) marks an op-set reviewed -- content-addressed by the
# sorted op-id set, so acking the same set twice is a no-op and, like claims (D8) and proposals
# (C10), the file *set* is the artifact: sync's union is a trivial file-level G-Set
# (`materialize._union_reviews`), no field merge.
_REVIEW_ART = _Artifact(("reviews",), committed=True)


def reviews_dir(repo: str | Path) -> Path:
    return subdir(repo, "reviews")


def review_rel(name: str) -> str:
    """The repo-relative path (`.sgt/reviews/<name>`) of one review file -- a blob read's key."""
    return "/".join((SGT_DIR, "reviews", name))


def save_review(repo: str | Path, name: str, body) -> None:
    """Write review file `name` (a full basename like `<review_id>.json`) to the working tree.
    Review records are immutable once acked; a re-ack of the same op-set overwrites the identical
    content-addressed key, a no-op on content."""
    _atomic_write_text(reviews_dir(repo) / name, _encode(body, _REVIEW_ART))


def load_review(repo: str | Path, name: str, default=None):
    """The logical body of review file `name` from the working tree, or `default` if absent."""
    p = reviews_dir(repo) / name
    if not p.is_file():
        return default
    return _unwrap(json.loads(p.read_text(encoding="utf-8")))


def list_review_files(repo: str | Path) -> list[str]:
    """Sorted basenames of every review file present in the working tree (empty if none)."""
    d = reviews_dir(repo)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file())


def load_blob_review(gb, sha: str, name: str, default=None):
    """The logical body of review file `name` as committed at `sha` (the historical-blob read
    path), or `default` if absent -- the same version dispatch as a working-tree read."""
    raw = gb.blob_bytes(sha, review_rel(name))
    if raw is None:
        return default
    return _unwrap(json.loads(raw.decode("utf-8")))
