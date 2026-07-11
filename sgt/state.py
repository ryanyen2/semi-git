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
from dataclasses import dataclass
from pathlib import Path

SGT_DIR = ".sgt"

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
    # local, gitignored -- per-clone, never travels, never read from a blob.
    "verdicts": _Artifact(("local", "oracle.json"), committed=False),
    "witness": _Artifact(("local", "witness.json"), committed=False),
    "ideal_table": _Artifact(("local", "ideal.json"), committed=False),
    "drafts": _Artifact(("local", "drafts.json"), committed=False),
    "staged": _Artifact(("local", "staged.json"), committed=False),
    "label_cache": _Artifact(("local", "label_cache.json"), committed=False, sort_keys=False, newline=False),
    "plan_sessions": _Artifact(("local", "plan_sessions.json"), committed=False),
    "plan_matches": _Artifact(("local", "plan_matches.json"), committed=False),
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
    both v0 (no envelope) and v1 payloads."""
    p = path(repo, name)
    if not p.is_file():
        return default
    return _unwrap(json.loads(p.read_text(encoding="utf-8")))


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
    p = path(repo, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_encode(body, art), encoding="utf-8")
