"""One owner for the `.sgt/` on-disk layout and its JSON schema envelope (plan U17, D3/C2).

Before this module, `.sgt/` layout knowledge was scattered across a dozen modules, each
hand-rolling its own path and `json.loads`/`write_text`, with no schema versioning. That is a
problem specific to sgt: `sync` reads committed artifacts (pins, declared, tree) not just from the
working tree but from *arbitrary historical git blobs* -- a teammate's ref tip can be any vintage
(`gb.blob_bytes(theirs_sha, ...)`). So an on-disk format is never retired from the read path; once
a new schema ships, every reader must keep parsing every schema any sgt version ever committed,
forever. Designing that in now -- before the collaboration artifacts (committed `ideal.json`,
`forks.json`, claims, proposals; U20-U24) exist -- is cheap; retrofitting it later is not.

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
    compact: bool = False  # no indent, tight separators -- only for large *local* sidecars,
    # where encode/parse time scales with the artifact and nothing diffs the file by eye.


# The registry: logical name -> layout. New collaboration artifacts (U20-U24: committed
# `ideal.json`, `forks.json`, `claims/`, `proposals/`) get their slot added here, all
# `committed=True`, and inherit the envelope + blob dispatch for free.
_ARTIFACTS: dict[str, _Artifact] = {
    # committed, team-shared -- read from arbitrary-vintage historical blobs by `sync`.
    "oracle_config": _Artifact(("oracle.json",), committed=True),
    "identity_constraints": _Artifact(("identity_constraints.json",), committed=True, sort_keys=False),
    "pins": _Artifact(("pins", "pins.json"), committed=True, sort_keys=False),
    "tree": _Artifact(("tree", "tree.json"), committed=True),
    # committed recovery record of a witness commit's own ideal (op-id list) -- survives a
    # squash/rebase that destroys `Sgt-Op:` trailers (C5). Distinct from the local, per-ref
    # `ideal_table` below (that stays authoritative for the *current* ref; this is in-tree history).
    "ideal": _Artifact(("ideal.json",), committed=True),
    # committed record of the open same-symbol forks a sync surfaced (C4) -- a fork is durable,
    # shared state that travels with the repo so a teammate's next sync (and `sgt status`) sees it.
    "forks": _Artifact(("forks.json",), committed=True),
    # committed collection of authored features (`sgt.lens.authored`, U6/R3/KTD3): af-id ->
    # {label, label_witness, member OR-Set (adds+tombstones)}. A user-authored named selection that
    # is first-class merged state, not a `tree.build` output -- merged field-by-field on sync (OR-Set
    # membership + witness-topo LWW label + carried af- id), so it travels and is read from
    # historical blobs like every committed slot.
    "authored_features": _Artifact(("authored", "features.json"), committed=True, sort_keys=False),
    # committed OR-Set of declared order edges (`sgt after`/`sgt after --retract`, U21/D6): adds
    # carry a unique tag, retraction tombstones observed tags, live = adds minus tombstoned.
    # `sgt.core.lens` resolves this down to the plain live edge set every consumer expects.
    "declared_orset": _Artifact(("declared_edges.json",), committed=True),
    # committed three-tier file-boundary overrides (`sgt tiers set`, U27/D4): explicit
    # entity/opaque/ignored patterns, the escape hatch over the built-in grammar-presence
    # default. Read from historical blobs at mining time (`sgt.core.tiers.load_tiers_at`) so
    # tier assignment stays a pure function of the mined commit (LAW-0).
    "tiers": _Artifact(("tiers.json",), committed=True),
    # committed intent-overlay prompt sidecar (`sgt.intent.prompts`, plan U3/KTD5): key (a
    # plan-id, session-name, or commit sha -- the same keys `Attribution` carries) -> prompt text.
    # Write-once per key, so sync's merge is a trivial G-Set union (`sgt.intent.prompts.merge`).
    "intent_prompts": _Artifact(("intent", "prompts.json"), committed=True, sort_keys=False),
    # committed intent-overlay LLM theme assignment (`sgt.intent.theme`, plan U4/KTD7): theme-id
    # -> label/rationale/member atom-shas/source. Rebuilt-on-sync like the feature tree (re-derive
    # over the merged op partition, never merged field-by-field) -- content-hash-keyed, so an
    # unchanged partition re-derives byte-identically and never re-pays or re-names (U5).
    "intent_themes": _Artifact(("intent", "themes.json"), committed=True, sort_keys=False),
    # committed feature-scoped intent segments (`sgt.intent.theme_segment`, the checkpoint model):
    # feature-id -> chronological list of {commit_shas, label, rationale, source}. Persists only the
    # LLM's boundary+label decision; op membership is re-derived deterministically from commit_shas
    # on read (KTD6). Rebuilt on demand by `sgt intent build`, same read-vs-build split as themes.
    "intent_segments": _Artifact(("intent", "segments.json"), committed=True, sort_keys=False),
    # committed user relabels of a checkpoint (`sgt intent relabel`): feature-id -> {first-commit-sha
    # -> label}. A separate layer from segments.json (LLM/deterministic boundaries+labels) so a user
    # edit survives `sgt intent build`. Keyed by the segment's first commit sha -- a stable-ish
    # identity: if a rebuild moves boundaries so that sha no longer starts a segment, the pin simply
    # doesn't match and is ignored, exactly like a stale feature-label pin.
    "intent_segment_pins": _Artifact(("intent", "segment_pins.json"), committed=True, sort_keys=False),
    # local, gitignored -- per-clone, never travels, never read from a blob.
    "verdicts": _Artifact(("local", "oracle.json"), committed=False),
    "witness": _Artifact(("local", "witness.json"), committed=False),
    "ideal_table": _Artifact(("local", "ideal.json"), committed=False),
    # committed, ref-carried per-ref exclusion OR-Set (1.1, promoted to shared in 1.2 §E):
    # {ref_key: {"adds": [[op_id, tag], ...], "tombstones": [tag, ...]}}. The *positive* record of
    # the ops an explicit edit (revert/pin, U8) removed -- the source of truth
    # `ideal(ref) = reduce(provenance-in-ancestry − exclusions)` derives from. Demotes `ideal_table`
    # to a cache: a reverted op stays gone across a git history rewrite (rebase/cherry-pick re-mines
    # the same content under a new sha) because the exclusion, not the mere absence from a trusted
    # table, is what subtracts it (F11/F20). Shared so the revert survives a *fresh clone's* cold
    # bootstrap (F20): the op is still in git history, so the clone's mine re-adds it by provenance
    # unless the exclusion travels on `refs/sgt/state`. Merges per-ref-key as an OR-Set on sync.
    "exclusions": _Artifact(("exclusions.json",), committed=True),
    # local, gitignored clustering/merge suggestion queue (U7): {id: record}, add-only until a
    # suggestion is accepted (via `sgt feature merge`/`split`/`move`) or dismissed. Local, not a
    # committed G-Set: suggestions are advisory and *dismissable* (a committed G-Set would need
    # tombstones), and a cross-clone conflict (U6) surfaces on the clone that ran the sync, which
    # is where it is resolved. Content-addressed id, so re-emitting the same suggestion is a no-op.
    "suggestions": _Artifact(("local", "suggestions.json"), committed=False),
    # local, gitignored per-ref mining-fidelity marks: {ref_key: [sha, ...]} recording the
    # witnessing commits whose mined ops `order.reduce_to_ideal` had to drop (a fork tip, an
    # ungrounded op). `grid_view` reads it to mark those commits "partial" instead of silently
    # omitting the loss (R6). Derived from *this* clone's own reduction pass -- never travels.
    "fidelity": _Artifact(("local", "fidelity.json"), committed=False),
    # local, gitignored per-ref genesis-backfill frontier: how far backward a backfill of
    # pre-horizon history has progressed for a given ref. Never travels -- like `witness` and
    # `ideal_table`, it's derived from *this* clone's own mining progress.
    "backfill": _Artifact(("local", "backfill.json"), committed=False),
    # local, gitignored per-ref no-op gate: {ref_key: {fp, ids}} recording the last `_sync`'s
    # fingerprint (HEAD + dirty source content + persisted ideal) and the ideal it produced, so an
    # unchanged tree short-circuits the O(files) dirty mining pass. Derived from this clone's own
    # working state -- never travels, like `witness`/`backfill`.
    "sync_cache": _Artifact(("local", "sync_cache.json"), committed=False),
    # local, gitignored per-ref UNIFIED operation-event log (`sgt.core.oplog`, U8/KTD6): the single
    # store `sgt undo` walks. It began (U26) as the ideal-edit journal -- `record_ideal` still
    # pushes the outgoing ideal (+ witness) as one `ideal_edit` event before each overwrite -- and
    # U8 *subsumed* it into this one log so every mutating verb's inverse is here (feature-reorg
    # snapshots, `after`, land/propose provenance), one pop per undo. Kept at the same slot/path so
    # historical local journals still load; an entry with no `kind` is read as `ideal_edit`. Never
    # travels.
    "ideal_journal": _Artifact(("local", "ideal_journal.json"), committed=False),
    "drafts": _Artifact(("local", "drafts.json"), committed=False),
    "staged": _Artifact(("local", "staged.json"), committed=False),
    "label_cache": _Artifact(("local", "label_cache.json"), committed=False, sort_keys=False, newline=False),
    "intent_cache": _Artifact(("local", "intent_cache.json"), committed=False, sort_keys=False, newline=False),
    "repair_cache": _Artifact(("local", "repair_cache.json"), committed=False, sort_keys=False, newline=False),
    "plan_sessions": _Artifact(("local", "plan_sessions.json"), committed=False),
    "plan_matches": _Artifact(("local", "plan_matches.json"), committed=False),
    # local, gitignored raw conversation-turn capture (`sgt.intent.turns`, intent-ledger M1):
    # {turn-id: record} keyed on the same plan-id/session-name/sha provenance keys `intent_prompts`
    # uses, but multi-turn, ordered, and kept-not-pruned -- the evidence layer reflection reasons
    # over to derive shareable rationale. Never travels (raw conversation stays on its machine), so
    # unlike `intent_prompts` it needs no merge; content-addressed, so re-capture is a no-op.
    "intent_turns": _Artifact(("local", "turns.json"), committed=False),
    # local, gitignored derived rationale records (`sgt.intent.rationale`, intent-ledger M1):
    # {rationale-id: record} -- reflection's answer to "why do these ops exist", transcribed from a
    # confirmed plan match over `intent_turns` evidence. Local in M1 (proves the bet with no sync
    # surface); the committed, CRDT-merged, liveness-joined team tier is M2, gated on the state-model
    # rework (2026-07-31-001 Phase 1.2). Content-addressed + append-only, so re-reflection is a no-op.
    "intent_rationale": _Artifact(("local", "rationale.json"), committed=False),
    # local, gitignored record of scratch-tree sessions (`sgt session start`, U30/D5): name ->
    # branch/scratch path/target branch/base op-ids/owning pid/start time. Per-clone, never
    # travels -- a session's scratch tree is a `git worktree` of *this* clone's object store.
    "sessions": _Artifact(("local", "sessions.json"), committed=False),
    # local, gitignored transactional-land journal (`sgt land`, U5/R7): `{ref, snapshot}` written
    # before the candidate tree is materialized and cleared on every landing/non-landing exit. A
    # crash mid-land leaves it behind, so the next `land` finds it and rolls the working tree back
    # to `snapshot`; `fsck` names the interrupted state. Per-clone, never travels.
    "land_pending": _Artifact(("local", "land_pending.json"), committed=False),
    # local, gitignored resume manifest for the v2->v3 op-store migration (`sgt migrate ops-v3`,
    # U10): written before the crossing (the old->new id map + recovered current ideal), cleared
    # after. Its presence means a migration is in flight, so a crashed `--apply` resumes from the
    # stored map rather than recomputing it against half-pruned op files. Per-clone, never travels.
    "migration_manifest": _Artifact(("local", "migration_manifest.json"), committed=False),
    # local, gitignored footprint-only sidecar over the op store (`sgt.core.opindex`): every op's
    # payload minus `images`, so read-only projection views skip `Store.all_ops()`'s per-op images
    # hex-decode (85%+ of the store's on-disk bytes) entirely. Self-healing (rebuilt on staleness),
    # never authoritative -- the ops directory always is. Per-clone, never travels. Compact: at
    # thousands of ops the pretty-printed encode/parse dominated every rebuild and every read.
    "op_index": _Artifact(("local", "op_index.json"), committed=False, compact=True),
    # local, gitignored cache of `sgt.entities.graph.build_entity_graph`'s edges at a given HEAD
    # sha (`sgt.lens.cluster`): that full-repo source parse is by far the costliest step in the
    # clustering signal build, yet its result is a pure function of HEAD alone -- so a no-op
    # refresh or small edit (HEAD unchanged) skips the reparse entirely. Self-healing (a sha
    # mismatch just triggers a rebuild), never authoritative. Per-clone, never travels.
    "structural_edge_cache": _Artifact(("local", "structural_edges.json"), committed=False),
    # local, gitignored cache of the fused coupling graph (`sgt.lens.tree.build`) that produced the
    # last-saved `tree.json`, tagged with that tree's own leaf-structure fingerprint. Lets the next
    # build detect cross-leaf coupling that gained/lost significance since then (Phase 2's
    # cross-edge dirtying trigger) without re-deriving the old graph. A fingerprint mismatch (a
    # foreign `previous`, e.g. from `reconcile`) or missing cache just means that trigger is
    # skipped for this build -- member-set dirtying still applies. Self-healing, never
    # authoritative, per-clone, never travels.
    "fused_snapshot": _Artifact(("local", "fused_snapshot.json"), committed=False),
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
