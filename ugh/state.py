"""The `.sgt/` layout: every JSON table's path, byte format and schema envelope.

Each payload is written as `{"schema": <int>, "data": <body>}`; a payload without the envelope is
read as v0, the body itself. Committed tables are also read from git blobs of any age (a teammate's
state ref), so readers accept every schema ever written. The op store (`.sgt/ops/`) has its own
codec in `ugh.core.store`.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

SGT_DIR = ".sgt"


def _atomic_write_text(p: Path, text: str) -> None:
    """Write through an fsync'd temp file renamed over `p`: a reader sees the old bytes or the new,
    and a crash leaves the old file intact."""
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

# Bump only when a table's content shape changes; readers keep accepting every prior version.
SCHEMA = 1


@dataclass(frozen=True)
class _Artifact:
    """A `.sgt/` JSON table's path and byte format. `committed` tables travel in git and are read
    from historical blobs; local ones are per-clone caches or records."""

    parts: tuple[str, ...]
    committed: bool
    sort_keys: bool = True
    newline: bool = True
    compact: bool = False  # for large local tables, where encode time matters and nobody diffs them


_ARTIFACTS: dict[str, _Artifact] = {
    # committed: the project's check (`ugh.config`)
    "oracle_config": _Artifact(("oracle.json",), committed=True),
    # committed: {op id: label} (`ugh.names`)
    "names": _Artifact(("names.json",), committed=True),
    # committed: entity/opaque/ignored path patterns, read at each mined commit (`ugh.core.tiers`)
    "tiers": _Artifact(("tiers.json",), committed=True),
    # committed: {key: prompt text}, write-once per key (`ugh.intent.prompts`)
    "intent_prompts": _Artifact(("intent", "prompts.json"), committed=True, sort_keys=False),
    # local, per clone, never read from a blob
    "verdicts": _Artifact(("local", "oracle.json"), committed=False),
    "witness": _Artifact(("local", "witness.json"), committed=False),
    # the miner version that wrote `witness` and `ideal_table`; another version's are stale
    "mined_by": _Artifact(("local", "mined_by.json"), committed=False),
    "ideal_table": _Artifact(("local", "ideal.json"), committed=False),
    # the no-op gate (`ugh.core.lens._sync`)
    "sync_cache": _Artifact(("local", "sync_cache.json"), committed=False),
    # the commits ugh made and the names snapshots, which `ugh undo` pops (`ugh.core.oplog`)
    "ideal_journal": _Artifact(("local", "ideal_journal.json"), committed=False),
    # {names: [label, ...], baseline: sha} (`ugh.names.declare`)
    "plan": _Artifact(("local", "plan.json"), committed=False),
    # conversation turns, kept on this machine (`ugh.intent.turns`)
    "intent_turns": _Artifact(("local", "turns.json"), committed=False),
    # the last tool events a hook appended (`ugh.intent.activity`)
    "intent_activity": _Artifact(("local", "activity.json"), committed=False),
    # per save, the turns and activity its capture window closed (`ugh.intent.manifest`)
    "intent_manifests": _Artifact(("local", "manifests.json"), committed=False, compact=True),
    # every op without its images, so reads skip decoding them (`ugh.core.opindex`)
    "op_index": _Artifact(("local", "op_index.json"), committed=False, compact=True),
    # tree-sitter parses keyed by content (`ugh.entities.extract`, `ugh.entities.graph`)
    "extract_cache": _Artifact(("local", "extract_cache.json"), committed=False, compact=True),
    "refs_cache": _Artifact(("local", "refs_cache.json"), committed=False, compact=True),
    # the last `.sgt/ops/` scan, trusted while the dir mtime is unchanged (`ugh.core.opindex`)
    "ops_dirstat": _Artifact(("local", "ops_dirstat.json"), committed=False),
    # digests of id sets already validated or reduced (`ugh.core.lens._derive_state`)
    "derive_cache": _Artifact(("local", "derive_cache.json"), committed=False),
    # {path: sha256} of `code(current_ideal)` for one ideal digest (`ugh.api.status_view`)
    "mat_manifest": _Artifact(("local", "mat_manifest.json"), committed=False, compact=True),
}


# -- artifact paths -------------------------------------------------------------------------------

def path(repo: str | Path, name: str) -> Path:
    """The absolute path of artifact `name` under this repo's `.sgt/`."""
    return Path(repo).joinpath(SGT_DIR, *_ARTIFACTS[name].parts)


def rel(name: str) -> str:
    """The artifact's repo-relative path (`.sgt/...`), the key a blob read uses."""
    return "/".join((SGT_DIR, *_ARTIFACTS[name].parts))


# -- schema envelope ------------------------------------------------------------------------------

def _unwrap(payload):
    """A table's body: the `data` of a `{"schema": <int>, "data": ...}` envelope, else the payload
    itself (v0)."""
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
    """The body of table `name` in the working tree, or `default` if absent. An unparsable local
    table reads as `default`, since it is rebuilt; an unparsable committed table raises."""
    p = path(repo, name)
    if not p.is_file():
        return default
    try:
        return _unwrap(json.loads(p.read_bytes()))
    except (json.JSONDecodeError, UnicodeDecodeError):
        if _ARTIFACTS[name].committed:
            raise
        return default


def decode_blob_json(raw: bytes | None, default=None):
    """`load_blob_json`'s decode step, for a blob the caller already fetched (or None)."""
    if raw is None:
        return default
    return _unwrap(json.loads(raw.decode("utf-8")))


def load_blob_json(gb, sha: str, name: str, default=None):
    """The body of table `name` as committed at `sha`, or `default` if absent."""
    raw = gb.blob_bytes(sha, rel(name))
    if raw is None:
        return default
    return _unwrap(json.loads(raw.decode("utf-8")))


def save_json(repo: str | Path, name: str, body) -> None:
    """Write artifact `name`'s `body` to the working tree, in its registered byte format."""
    art = _ARTIFACTS[name]
    _atomic_write_text(path(repo, name), _encode(body, art))


def save_json_if_changed(repo: str | Path, name: str, body) -> bool:
    """`save_json` unless the bytes on disk are already identical; returns whether it wrote. Editor
    file watchers refresh on any `.sgt/` write, so a no-op write would loop."""
    art = _ARTIFACTS[name]
    text = _encode(body, art)
    p = path(repo, name)
    if p.is_file() and p.read_text(encoding="utf-8") == text:
        return False
    _atomic_write_text(p, text)
    return True
