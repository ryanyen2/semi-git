"""Three-tier file boundary (plan U27, D4): every path mined resolves to a tier -- `entity`
(parsed via tree-sitter grammar), `opaque` (whole-file pseudo-symbol -- today's implicit
fallback for anything with no grammar), or `ignored` (excluded from mining entirely).
Configuration layers, highest wins: `.sgt/tiers.json` (committed, explicit per-pattern
overrides -- the escape hatch) > `.sgtignore` (gitignore-style, ignored-tier only, committed)
> the built-in default (`entity` if a tree-sitter grammar exists for the path, else `opaque`,
i.e. today's behavior made explicit).

LAW-0 (mining determinism): resolving a path's tier reads `.sgt/tiers.json`/`.sgtignore` as
committed *in the mined commit's own tree* (`load_tiers_at`), never the current working tree
-- so tier assignment stays a pure function of the commit, and two replicas with divergent
working tier maps re-mine identical history to byte-identical ops.

`.sgtignore` is not a `.gitignore` clone: anything `.gitignore` already excludes is never
committed, so `mine.py` (which only ever diffs committed blobs) never sees it anyway.
`.sgtignore` exists for paths that *are* committed but which sgt should never mine (vendored
code, generated artifacts someone chose to check in).

Pattern matching here is a small, deliberately non-exhaustive gitignore-style glob (`fnmatch`
against the full repo-relative path or the basename, plus a trailing `/` meaning "anything
under this directory") -- not full gitignore semantics (no negation, no `**`). Good enough for
excluding a vendored subtree or a class of generated files; anything more exotic uses
`.sgt/tiers.json`'s explicit per-path override instead.
"""

from __future__ import annotations

import fnmatch
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from sgt import state
from sgt.entities.extract import _language_for

# Basenames that are near-universally generated/vendored lockfiles -- collapsed under the
# `derived` flag (S4) so review surfaces can fold them away, regardless of tier. Domain
# knowledge (package-manager conventions), not measured against this repo's own history -- no
# locally sampled repo happened to have one tracked.
DERIVED_BASENAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
    "Cargo.lock", "Gemfile.lock", "composer.lock", "uv.lock", "go.sum",
})

_TIERS = ("entity", "opaque", "ignored")
_EMPTY_OVERRIDES: dict[str, tuple[str, ...]] = {"entity": (), "opaque": (), "ignored": ()}


@dataclass(frozen=True)
class TierConfig:
    """One commit's (or the working tree's) effective tier configuration. `overrides` maps each
    tier name to its ordered pattern tuple (`.sgt/tiers.json`); conflict priority when a path
    matches more than one tier's patterns is `ignored` > `opaque` > `entity` -- the most
    exclusionary match wins. `sgtignore` is `.sgtignore`'s patterns (ignored-tier only)."""

    overrides: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(_EMPTY_OVERRIDES))
    sgtignore: tuple[str, ...] = ()


EMPTY = TierConfig()


def _match_one(path: str, pattern: str) -> bool:
    pattern = pattern.strip()
    if not pattern or pattern.startswith("#"):
        return False
    if pattern.endswith("/"):
        prefix = pattern.rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if "/" in pattern:
        return fnmatch.fnmatch(path, pattern)
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)


def _match_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_match_one(path, p) for p in patterns)


def _parse_sgtignore(text: str) -> tuple[str, ...]:
    return tuple(line for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def _parse_tiers_json(body) -> dict[str, tuple[str, ...]]:
    overrides = dict(_EMPTY_OVERRIDES)
    if isinstance(body, dict):
        for tier in _TIERS:
            patterns = body.get(tier)
            if isinstance(patterns, list):
                overrides[tier] = tuple(str(p) for p in patterns)
    return overrides


def load_tiers_at(gb, sha: str) -> TierConfig:
    """`TierConfig` as committed at `sha` -- the mining-time read (LAW-0): never the working
    tree, so tier assignment stays a pure function of the mined commit. `sha` may be any
    tree-ish `GitBinding` accepts, including the dirty-pass synthetic snapshot tree."""
    return load_tiers_at_many(gb, [sha])[sha]


# Memo: a tree-ish's committed tier config is immutable (a commit sha or a content-addressed
# snapshot tree fixes `.sgt/tiers.json` + `.sgtignore` forever), yet the rebirth lookback and
# per-commit mining re-read the same pair for the same trees hundreds of times per history
# chunk. Bounded LRU keyed by (repo, tree-ish).
_TIERS_AT_CACHE: "OrderedDict[tuple, TierConfig]" = OrderedDict()
_TIERS_AT_CACHE_MAX = 4096


def load_tiers_at_many(gb, shas: list[str]) -> dict[str, TierConfig]:
    """`load_tiers_at` for several `sha`s in one batched blob read instead of two per `sha`
    (`.sgt/tiers.json` + `.sgtignore`) -- mining a commit needs this for both the commit and
    its parent. Memoized per (repo, tree-ish): a committed config never changes for a given
    tree, so only never-seen trees pay the blob read at all."""
    repo_key = gb.repo_key()
    out: dict[str, TierConfig] = {}
    misses: list[str] = []
    for sha in shas:
        cached = _TIERS_AT_CACHE.get((repo_key, sha))
        if cached is not None:
            _TIERS_AT_CACHE.move_to_end((repo_key, sha))
            out[sha] = cached
        else:
            misses.append(sha)
    if not misses:
        return out
    tiers_path = state.rel("tiers")
    specs = [(sha, path) for sha in misses for path in (tiers_path, ".sgtignore")]
    blobs = gb.blob_bytes_many(specs)
    for i, sha in enumerate(misses):
        tiers_raw, ignore_raw = blobs[2 * i], blobs[2 * i + 1]
        tiers_body = state.decode_blob_json(tiers_raw, default=None)
        overrides = _parse_tiers_json(tiers_body) if tiers_body is not None else dict(_EMPTY_OVERRIDES)
        sgtignore = _parse_sgtignore(ignore_raw.decode("utf-8")) if ignore_raw is not None else ()
        cfg = TierConfig(overrides=overrides, sgtignore=sgtignore)
        out[sha] = cfg
        _TIERS_AT_CACHE[(repo_key, sha)] = cfg
        if len(_TIERS_AT_CACHE) > _TIERS_AT_CACHE_MAX:
            _TIERS_AT_CACHE.popitem(last=False)
    return out


def load_tiers(repo) -> TierConfig:
    """`TierConfig` from the working tree -- for CLI reporting/mutation, never mining."""
    tiers_body = state.load_json(repo, "tiers", default=None)
    overrides = _parse_tiers_json(tiers_body) if tiers_body is not None else dict(_EMPTY_OVERRIDES)
    ignore_path = Path(repo) / ".sgtignore"
    sgtignore = _parse_sgtignore(ignore_path.read_text(encoding="utf-8")) if ignore_path.is_file() else ()
    return TierConfig(overrides=overrides, sgtignore=sgtignore)


def resolve_tier(path: str, cfg: TierConfig) -> str:
    """The tier `path` resolves to under `cfg`: explicit override (`ignored` > `opaque` >
    `entity` conflict priority) > `.sgtignore` (ignored only) > the built-in default -- `entity`
    if a tree-sitter grammar exists for `path`, else `opaque` (today's implicit behavior, made
    explicit). An `entity` override for a path with no grammar silently degrades to `opaque` --
    the promotion is a no-op until this kernel actually gains that language's grammar, so setting
    or clearing the override has no effect on mining either way."""
    if _match_any(path, cfg.overrides.get("ignored", ())):
        return "ignored"
    if _match_any(path, cfg.overrides.get("opaque", ())):
        return "opaque"
    if _match_any(path, cfg.overrides.get("entity", ())):
        return "entity" if _language_for(path) is not None else "opaque"
    if _match_any(path, cfg.sgtignore):
        return "ignored"
    return "entity" if _language_for(path) is not None else "opaque"


def is_derived(path: str) -> bool:
    """S4: derived (generated/vendored-lockfile) files carry a flag review surfaces collapse --
    independent of tier, so a lockfile stays `opaque` (or `ignored`, if the user chooses) but is
    still marked for UI folding."""
    return path.rsplit("/", 1)[-1] in DERIVED_BASENAMES
