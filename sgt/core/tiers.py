"""Three-tier file boundary (plan U27, D4): every path mined resolves to a tier -- `entity`
(parsed via tree-sitter grammar), `opaque` (whole-file pseudo-symbol -- today's implicit
fallback for anything with no grammar), or `ignored` (excluded from mining entirely).
Configuration layers, highest wins: `.sgt/tiers.json` (committed, explicit per-pattern
overrides -- the escape hatch) > two default exclusions (any dot-path, and anything the repo's
`.gitignore` matches) > `.sgtignore` (gitignore-style, ignored-tier only, committed) > the
built-in default (`entity` if a tree-sitter grammar exists for the path, else `opaque`).

Default exclusions (D4, launch): sgt is a lens over *source you author*, not the tooling and
config that surrounds it. Two classes are `ignored` by default, so they never mint one-file
"features":
  1. Dot-paths -- any path with a leading-dot component (`.gitignore`, `.github/…`, `.vscode/…`,
     `.claude/…`, `.mcp.json`, and sgt's own `.sgt/…`). Pure function of the path.
  2. `.gitignore` matches -- honored even for *tracked* files. Files committed before a
     `.gitignore` rule existed (a `.claude/` or `build/` checked in, then later ignored) still
     reach the miner as tracked blobs; git wouldn't re-ignore them, but sgt should. This goes
     deliberately beyond git's tracked-file semantics.
Either default is overridable: an `entity`/`opaque` pattern in `.sgt/tiers.json` (checked first)
force-includes a path sgt would otherwise skip.

LAW-0 (mining determinism): resolving a path's tier reads `.sgt/tiers.json`/`.sgtignore`/
`.gitignore` as committed *in the mined commit's own tree* (`load_tiers_at`), never the current
working tree -- so tier assignment stays a pure function of the commit, and two replicas with
divergent working configs re-mine identical history to byte-identical ops. (A non-dot file that
gains a `.gitignore` rule mid-history is therefore mined before that commit and closed after --
the standard tier-transition path in `mine.py`; dot-paths are commit-independent.)

`.sgtignore` remains for the reverse case: paths that are *not* dot-paths and *not* gitignored,
but which sgt still shouldn't mine (a vendored subtree, generated artifacts checked in on
purpose). Its matcher is the same compact gitignore engine below.

Pattern matching is a compact gitignore engine (`_gitignore_ignored`): comments, `!` negation
(ordered -- last match wins), leading-`/` and embedded-`/` anchoring, trailing-`/` dir-only,
and `*`/`?`/`**` globs with `*` not crossing `/`. Only the repo-root `.gitignore` is read (no
per-directory nested ignore files) -- that keeps a tier a pure function of one blob per tree.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
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
    exclusionary match wins. `sgtignore` is `.sgtignore`'s patterns; `gitignore` is the repo-root
    `.gitignore`'s patterns -- both ignored-tier only, both run through the same engine."""

    overrides: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(_EMPTY_OVERRIDES))
    sgtignore: tuple[str, ...] = ()
    gitignore: tuple[str, ...] = ()


EMPTY = TierConfig()


def _has_dot_component(path: str) -> bool:
    """A path with any leading-dot component -- `.gitignore`, `.github/x`, `sub/.cache/y`. The
    first default exclusion; a pure function of the path (no config), so it's commit-independent."""
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
    """`TierConfig` from the working tree -- for CLI reporting/mutation, never mining."""
    tiers_body = state.load_json(repo, "tiers", default=None)
    overrides = _parse_tiers_json(tiers_body) if tiers_body is not None else dict(_EMPTY_OVERRIDES)
    ignore_path = Path(repo) / ".sgtignore"
    sgtignore = _parse_patterns(ignore_path.read_text(encoding="utf-8")) if ignore_path.is_file() else ()
    gitignore_path = Path(repo) / ".gitignore"
    gitignore = _parse_patterns(gitignore_path.read_text(encoding="utf-8")) if gitignore_path.is_file() else ()
    return TierConfig(overrides=overrides, sgtignore=sgtignore, gitignore=gitignore)


def resolve_tier(path: str, cfg: TierConfig) -> str:
    """The tier `path` resolves to under `cfg`: explicit override (`ignored` > `opaque` >
    `entity` conflict priority) > the two default exclusions (dot-path, `.gitignore` match) >
    `.sgtignore` (ignored only) > the built-in default -- `entity` if a tree-sitter grammar
    exists for `path`, else `opaque`. The overrides are checked first, so an `entity`/`opaque`
    pattern in `.sgt/tiers.json` force-includes a dot-path or gitignored path sgt would otherwise
    skip. An `entity` override for a path with no grammar silently degrades to `opaque`."""
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
    """S4: derived (generated/vendored-lockfile) files carry a flag review surfaces collapse --
    independent of tier, so a lockfile stays `opaque` (or `ignored`, if the user chooses) but is
    still marked for UI folding."""
    return path.rsplit("/", 1)[-1] in DERIVED_BASENAMES
