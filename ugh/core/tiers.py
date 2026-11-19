"""Every mined path resolves to a tier: `entity` (split by its tree-sitter grammar), `opaque` (one
whole-file symbol) or `ignored` (not mined). Highest first: the patterns in `.sgt/tiers.json`;
then dot-paths and paths the root `.gitignore` matches, even tracked ones; then `.sgtignore`; then
`entity` when a grammar exists for the path, else `opaque`.

Mining reads these files as committed in the mined commit's own tree (`load_tiers_at`), so a tier
is a function of the commit and every clone mines the same ops. Patterns follow a compact subset
of gitignore (`_gitignore_ignored`), and only the root `.gitignore` is read.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ugh import state
from ugh.entities.extract import _language_for

# Lockfile basenames, flagged `derived` whatever their tier.
DERIVED_BASENAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
    "Cargo.lock", "Gemfile.lock", "composer.lock", "uv.lock", "go.sum",
})

_TIERS = ("entity", "opaque", "ignored")
_EMPTY_OVERRIDES: dict[str, tuple[str, ...]] = {"entity": (), "opaque": (), "ignored": ()}


@dataclass(frozen=True)
class TierConfig:
    """One commit's (or the working tree's) tier configuration. `overrides` maps each tier to its
    patterns from `.sgt/tiers.json`, the most exclusionary match winning; `sgtignore` and
    `gitignore` are patterns for `ignored` only."""

    overrides: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(_EMPTY_OVERRIDES))
    sgtignore: tuple[str, ...] = ()
    gitignore: tuple[str, ...] = ()


EMPTY = TierConfig()


def _has_dot_component(path: str) -> bool:
    """A path with any leading-dot component: `.gitignore`, `.github/x`, `sub/.cache/y`."""
    return any(part.startswith(".") for part in path.split("/") if part)


@lru_cache(maxsize=2048)
def _pattern_regex(pattern: str) -> "re.Pattern | None":
    """Compile one gitignore pattern (already stripped of a leading `!`) to a full-path regex, or
    None for a blank/comment line. Compact subset: leading-`/` or embedded-`/` anchors to the repo
    root, a trailing `/` restricts to a directory (but every match also covers descendants, since
    the miner only ever sees files under a matched dir), `*`/`?` don't cross `/`, and `**` does."""
    if not pattern or pattern.startswith("#"):
        return None
    pattern = pattern.rstrip("/")  # dir-only vs file is immaterial: we always allow descendants
    anchored = pattern.startswith("/") or ("/" in pattern.strip("/"))
    pattern = pattern.strip("/")
    out, i, n = [], 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 2] == "**":
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
                    out.append("(?:[^/]+/)*")  # `**/`: zero or more path segments
                else:
                    out.append(".*")  # trailing `**`: anything, including `/`
            else:
                out.append("[^/]*")  # `*`: anything but a separator
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    prefix = "" if anchored else "(?:.*/)?"  # unanchored patterns match at any depth
    return re.compile("^" + prefix + "".join(out) + "(?:/.*)?$")


def _gitignore_ignored(path: str, patterns: tuple[str, ...]) -> bool:
    """Does `path` match `patterns` under gitignore semantics? Ordered evaluation -- the last
    pattern to match decides, and a `!`-prefixed pattern flips a match back to not-ignored."""
    ignored = False
    for raw in patterns:
        line = raw.rstrip("\n")
        neg = line.startswith("!")
        rx = _pattern_regex(line[1:] if neg else line)
        if rx is not None and rx.match(path):
            ignored = not neg
    return ignored


def _parse_patterns(text: str) -> tuple[str, ...]:
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
    """`TierConfig` as committed at `sha`, any tree-ish `GitBinding` accepts (the dirty pass
    passes a snapshot tree). Mining reads this, never the working tree."""
    return load_tiers_at_many(gb, [sha])[sha]


# (repo, tree-ish) -> its config, which a tree-ish fixes forever.
_TIERS_AT_CACHE: "OrderedDict[tuple, TierConfig]" = OrderedDict()
_TIERS_AT_CACHE_MAX = 4096


def load_tiers_at_many(gb, shas: list[str]) -> dict[str, TierConfig]:
    """`load_tiers_at` for several `sha`s in one batched blob read, memoized per tree-ish."""
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
    reads = (tiers_path, ".sgtignore", ".gitignore")
    specs = [(sha, path) for sha in misses for path in reads]
    blobs = gb.blob_bytes_many(specs)
    for i, sha in enumerate(misses):
        base = len(reads) * i
        tiers_raw, ignore_raw, gitignore_raw = blobs[base], blobs[base + 1], blobs[base + 2]
        tiers_body = state.decode_blob_json(tiers_raw, default=None)
        overrides = _parse_tiers_json(tiers_body) if tiers_body is not None else dict(_EMPTY_OVERRIDES)
        sgtignore = _parse_patterns(ignore_raw.decode("utf-8")) if ignore_raw is not None else ()
        gitignore = _parse_patterns(gitignore_raw.decode("utf-8")) if gitignore_raw is not None else ()
        cfg = TierConfig(overrides=overrides, sgtignore=sgtignore, gitignore=gitignore)
        out[sha] = cfg
        _TIERS_AT_CACHE[(repo_key, sha)] = cfg
        if len(_TIERS_AT_CACHE) > _TIERS_AT_CACHE_MAX:
            _TIERS_AT_CACHE.popitem(last=False)
    return out


def load_tiers(repo) -> TierConfig:
    """`TierConfig` from the working tree, for reads of it; mining uses `load_tiers_at`."""
    tiers_body = state.load_json(repo, "tiers", default=None)
    overrides = _parse_tiers_json(tiers_body) if tiers_body is not None else dict(_EMPTY_OVERRIDES)
    ignore_path = Path(repo) / ".sgtignore"
    sgtignore = _parse_patterns(ignore_path.read_text(encoding="utf-8")) if ignore_path.is_file() else ()
    gitignore_path = Path(repo) / ".gitignore"
    gitignore = _parse_patterns(gitignore_path.read_text(encoding="utf-8")) if gitignore_path.is_file() else ()
    return TierConfig(overrides=overrides, sgtignore=sgtignore, gitignore=gitignore)


def resolve_tier(path: str, cfg: TierConfig) -> str:
    """The tier `path` resolves to under `cfg`, in the order the module docstring gives. An
    `entity` override for a path with no grammar is `opaque`."""
    if _gitignore_ignored(path, cfg.overrides.get("ignored", ())):
        return "ignored"
    if _gitignore_ignored(path, cfg.overrides.get("opaque", ())):
        return "opaque"
    if _gitignore_ignored(path, cfg.overrides.get("entity", ())):
        return "entity" if _language_for(path) is not None else "opaque"
    if _has_dot_component(path) or _gitignore_ignored(path, cfg.gitignore):
        return "ignored"
    if _gitignore_ignored(path, cfg.sgtignore):
        return "ignored"
    return "entity" if _language_for(path) is not None else "opaque"


def is_derived(path: str) -> bool:
    """Whether `path` is a lockfile, flagged so a view can fold it away whatever its tier."""
    return path.rsplit("/", 1)[-1] in DERIVED_BASENAMES
