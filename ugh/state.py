"""One owner for the `.sgt/` on-disk layout and its JSON schema envelope (plan U17, D3/C2).

Before this module, `.sgt/` layout knowledge was scattered across a dozen modules, each
hand-rolling its own path and `json.loads`/`write_text`, with no schema versioning. That is a
problem specific to ugh: `sync` reads committed artifacts (names, prompts) not just from the
working tree but from *arbitrary historical git blobs* -- a teammate's ref tip can be any vintage
(`gb.blob_bytes(theirs_sha, ...)`). So an on-disk format is never retired from the read path; once
a new schema ships, every reader must keep parsing every schema any ugh version ever committed,
forever. Designing that in early is cheap; retrofitting it later is not.

Every JSON artifact's payload is wrapped in a uniform envelope, `{"schema": <int>, "data": <body>}`.
A payload lacking that envelope is implicitly v0 (the pre-U17 shape, byte-for-byte what is committed
in every real repo's history today). `load_*` accept v0 *and* v1 (dispatch on the envelope); `dump`/
`save_json` emit the current version. The same decoder serves a working-tree read and a
blob-at-SHA read (`load_blob_json`).

Only the small JSON tables route through here. The content-addressed op store (`.sgt/ops/`) keeps
its own atomic-write + fsck codec in `ugh.core.store` -- it is not a "small JSON table" and its
content-addressing already gives it cross-version stability.
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
    compact: bool = False  # no indent, tight separators -- only for large *local* sidecars,
    # where encode/parse time scales with the artifact and nothing diffs the file by eye.


# The registry: logical name -> layout. A committed artifact inherits the envelope + blob dispatch
# for free.
_ARTIFACTS: dict[str, _Artifact] = {
    # committed, team-shared -- read from arbitrary-vintage historical blobs by `sync`.
    "oracle_config": _Artifact(("oracle.json",), committed=True),
    # committed names a person or agent gave (`ugh.names`): {op id: label}, merged three-way per op.
    "names": _Artifact(("names.json",), committed=True),
    # committed three-tier file-boundary overrides (`ugh tiers set`, U27/D4): explicit
    # entity/opaque/ignored patterns, the escape hatch over the built-in grammar-presence
    # default. Read from historical blobs at mining time (`ugh.core.tiers.load_tiers_at`) so
    # tier assignment stays a pure function of the mined commit (LAW-0).
    "tiers": _Artifact(("tiers.json",), committed=True),
    # committed intent-overlay prompt sidecar (`ugh.intent.prompts`, plan U3/KTD5): key (a
    # plan-id, session-name, or commit sha -- the same keys `Attribution` carries) -> prompt text.
    # Write-once per key, so sync's merge is a trivial G-Set union (`ugh.intent.prompts.merge`).
    "intent_prompts": _Artifact(("intent", "prompts.json"), committed=True, sort_keys=False),
    # local, gitignored -- per-clone, never travels, never read from a blob.
    "verdicts": _Artifact(("local", "oracle.json"), committed=False),
    "witness": _Artifact(("local", "witness.json"), committed=False),
    # local: the miner version that wrote `witness` and `ideal_table`; another version's are stale.
    "mined_by": _Artifact(("local", "mined_by.json"), committed=False),
    "ideal_table": _Artifact(("local", "ideal.json"), committed=False),
    # local, gitignored per-ref no-op gate: {ref_key: {fp, ids}} recording the last `_sync`'s
    # fingerprint (HEAD + dirty source content + persisted ideal) and the ideal it produced, so an
    # unchanged tree short-circuits the O(files) dirty mining pass. Derived from this clone's own
    # working state -- never travels, like `witness`.
    "sync_cache": _Artifact(("local", "sync_cache.json"), committed=False),
    # local, gitignored per-ref operation log (`ugh.core.oplog`): the commits ugh made and the
    # names snapshots, which `ugh undo` pops one at a time. Never travels.
    "ideal_journal": _Artifact(("local", "ideal_journal.json"), committed=False),
    # local, gitignored plan (`ugh.api.plan_view`): {names: [label, ...], baseline: sha}.
    "plan": _Artifact(("local", "plan.json"), committed=False),
    # local, gitignored raw conversation-turn capture (`ugh.intent.turns`, intent-ledger M1):
    # {turn-id: record} keyed on the same plan-id/session-name/sha provenance keys `intent_prompts`
    # uses, but multi-turn, ordered, and kept-not-pruned -- the evidence `ugh show` quotes the ask
    # behind a save from. Never travels (raw conversation stays on its machine), so
    # unlike `intent_prompts` it needs no merge; content-addressed, so re-capture is a no-op.
    "intent_turns": _Artifact(("local", "turns.json"), committed=False),
    # local, gitignored agent-action feed (`ugh.intent.activity`): a bounded ring buffer of the
    # last N tool events (one row per Edit/Write) the `PostToolUse` hook appends, which each save's
    # capture manifest copies its window of. Unlike turns it is NOT content-
    # addressed (repeated edits to one file are distinct rows) and is trimmed to a fixed cap, so it
    # never grows unbounded. Never travels.
    "intent_activity": _Artifact(("local", "activity.json"), committed=False),
    # local, gitignored per-save capture manifests (`ugh.intent.manifest`, capture weave P1,
    # docs/design/2026-09-01-capture-weave.md §4b): {commit-sha: record} -- the durable copy of the
    # capture window each save closed (its turns, its activity events, and the (sha, footprint)
    # anchors of the ops it minted). Written once per save at the save beat, never rewritten; what
    # makes the activity ring buffer's trim a liveness detail instead of data loss, and what the
    # stint derivation (P2) reads instead of a batch aligner. Verbatim conversation, so local
    # forever like `intent_turns`; compact -- it grows with every save.
    "intent_manifests": _Artifact(("local", "manifests.json"), committed=False, compact=True),
    # local, gitignored footprint-only sidecar over the op store (`ugh.core.opindex`): every op's
    # payload minus `images`, so read-only projection views skip `Store.all_ops()`'s per-op images
    # hex-decode (85%+ of the store's on-disk bytes) entirely. Self-healing (rebuilt on staleness),
    # never authoritative -- the ops directory always is. Per-clone, never travels. Compact: at
    # thousands of ops the pretty-printed encode/parse dominated every rebuild and every read.
    "op_index": _Artifact(("local", "op_index.json"), committed=False, compact=True),
    # local, gitignored disk backing for the content-addressed tree-sitter caches
    # (`ugh.entities.extract._EXTRACT_CACHE` / `ugh.entities.graph._REFS_CACHE`): the live tree's
    # per-file parses, so a fresh process's dirty mining pass re-parses only the files that
    # actually changed instead of the whole tree. Content-addressed keys make staleness
    # structural; a bad entry degrades to a re-parse. Per-clone, never travels.
    "extract_cache": _Artifact(("local", "extract_cache.json"), committed=False, compact=True),
    "refs_cache": _Artifact(("local", "refs_cache.json"), committed=False, compact=True),
    # local, gitignored racy-clean record of the last full `.sgt/ops/` dirent scan
    # (`ugh.core.opindex._ops_dir_stat`): the ops dir's mtime, its dirent count + max entry mtime,
    # and the wall-clock instant the scan *started*. A reader that finds the dir mtime unchanged
    # AND the recorded scan started comfortably after that mtime can trust the recorded
    # (count, max_mtime) without re-listing ~20k dirents -- any later write must stamp the dir
    # with a strictly newer mtime. Self-healing (a mismatch just rescans), never authoritative.
    "ops_dirstat": _Artifact(("local", "ops_dirstat.json"), committed=False),
    # local, gitignored derivation stamps (`ugh.core.lens._derive_state`): digests of id sets
    # whose grounding/fork-freedom validation or reduction already ran -- pure functions of the
    # content-addressed ids, so the next process can skip re-deriving them. Presence of the ops
    # themselves is always re-checked live. Self-healing, never authoritative, never travels.
    "derive_cache": _Artifact(("local", "derive_cache.json"), committed=False),
    # local, gitignored `{path: sha256}` manifest of `code(current_ideal)` keyed by the ideal's
    # id-set digest + miner version (`api.status_view`): drift detection and the skips read need
    # only per-path byte-equality and path membership, so a repeat status over an unchanged ideal
    # skips loading frontier images entirely. Self-healing, never authoritative, never travels.
    "mat_manifest": _Artifact(("local", "mat_manifest.json"), committed=False, compact=True),
}


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
    if art.compact:
        text = json.dumps(envelope, separators=(",", ":"), sort_keys=art.sort_keys)
    else:
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
        # Bytes straight into the parser: `json.loads` decodes UTF-8 itself, so the multi-MB
        # sidecars (op_index, extract_cache) skip materializing an intermediate `str`.
        return _unwrap(json.loads(p.read_bytes()))
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


def save_json_if_changed(repo: str | Path, name: str, body) -> bool:
    """Like `save_json`, but skips the write when the encoded bytes are byte-identical to what's
    already on disk. Returns whether a write happened. Some artifacts (the map rebuild, the label
    cache) get recomputed on every read even when nothing changed; an unconditional write there
    bumps the file's mtime for no reason, and these are `.sgt/**/*.json` paths a client's file
    watcher invalidates its cache on -- a no-op rewrite makes a no-op read retrigger another
    refresh, forever."""
    art = _ARTIFACTS[name]
    text = _encode(body, art)
    p = path(repo, name)
    if p.is_file() and p.read_text(encoding="utf-8") == text:
        return False
    _atomic_write_text(p, text)
    return True
