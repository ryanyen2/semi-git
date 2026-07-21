"""Rung 2 of the intent overlay's fallback ladder (plan U4, KTD4/KTD7): `IntentThemer` names
every scope bundle and coalesces the scope-less atoms `sgt.intent.group.scope_bundles` couldn't
place, mirroring `sgt.lens.label.Labeler`'s structure exactly -- content-hash cache, deterministic
offline fallback, `source` tagging, retry-on-fallback.

The critical invariant this module exists to protect: **the LLM never emits op-ids**. Its pydantic
output schemas carry commit shas only, and `atom_shas` is validated as a subset of the shas it was
shown -- a hallucinated or invented sha is dropped, never persisted. Membership of a *scope* bundle
is never LLM-decided at all (it comes straight from `group.scope_bundles`, rung 1); only a
scope-less atom's *placement* and every theme's *name* are. This is what keeps `sgt intent revert`
safe to drive from a theme boundary (KTD6): a wrong LLM call can misname or mis-group a handful of
otherwise-unbundled atoms, but it can never smuggle an op-id into the deterministic union a revert
resolves against.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel

from sgt import state
from sgt.config import get_client, get_model
from sgt.intent._guard import filter_to_shown
from sgt.intent.group import Bundle, IntentAtom, _atom_sort_key

EFFORT = "low"
MAX_ATOMS = 40  # keeps the scope-less coalescing prompt bounded on a large store


class ThemeLabel(BaseModel):
    label: str  # 2-5 words, human-facing theme name
    rationale: str  # one line: what this group of commits was for


class ThemeGroup(BaseModel):
    label: str
    rationale: str
    atom_shas: list[str]  # must be a subset of the scope-less atoms shown -- validated, never trusted


class ThemeGroups(BaseModel):
    groups: list[ThemeGroup]


def _hash_key(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()[:12]


def _bundle_key(bundle: Bundle) -> str:
    """Content-hash of a scope bundle's identity: scope + every member atom's sha and subject,
    sorted. Unchanged membership -> unchanged key -> a cache hit that never re-pays or re-names
    (the exact flicker [[label-cache-and-rebuild-architecture]] already fixed once)."""
    parts = [bundle.scope or ""]
    for atom in sorted(bundle.atoms, key=lambda a: a.commit_sha):
        parts.extend((atom.commit_sha, atom.subject))
    return _hash_key(*parts)


def _scopeless_key(atoms: list[IntentAtom]) -> str:
    parts: list[str] = []
    for atom in sorted(atoms, key=lambda a: a.commit_sha):
        parts.extend((atom.commit_sha, atom.subject))
    return _hash_key("\x01scopeless", *parts)


def theme_id_for(atom_shas: frozenset[str]) -> str:
    """A theme's id is content-addressed over its member shas (sorted) -- like the tree's own
    birth-id feature ids, deterministic and stable across independent rebuilds, never an
    incrementing counter that would drift the moment two builds group things differently."""
    return "theme-" + hashlib.sha1("\x00".join(sorted(atom_shas)).encode()).hexdigest()[:12]


def _fallback_bundle_label(bundle: Bundle) -> ThemeLabel:
    label = bundle.scope or "misc"
    return ThemeLabel(label=label, rationale=f"Commits declaring scope {label!r} (no LLM label available).")


def _fallback_scopeless_groups(atoms: list[IntentAtom]) -> list[ThemeGroup]:
    """Zero-network fallback: every scope-less atom stays its own singleton, labeled from its
    commit subject (already a ready-made human label -- KTD2)."""
    return [
        ThemeGroup(label=(atom.subject or atom.commit_sha[:8])[:60], rationale="Ungrouped commit (no LLM available).", atom_shas=[atom.commit_sha])
        for atom in atoms
    ]


class IntentThemer:
    def __init__(self, repo: str | Path = ".") -> None:
        self._repo = repo
        self._client = None
        self.cache: dict = state.load_json(repo, "intent_cache", default={})
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0
        self.scopeless_chunk_key_by_sha: dict[str, str] = {}  # populated by group_scopeless (U6/R8)

    @property
    def client(self):
        if self._client is None:
            self._client = get_client(self._repo)
        return self._client

    def _request(self, prompt: str, schema):
        r = self.client.responses.parse(
            model=get_model(self._repo), input=prompt, text_format=schema,
            reasoning={"effort": EFFORT},
        )
        self.calls += 1
        self.tokens_in += r.usage.input_tokens
        self.tokens_out += r.usage.output_tokens
        return r.output_parsed

    def label_bundle(self, bundle: Bundle) -> ThemeLabel:
        """Name one scope bundle. Membership is never decided here -- only `label`/`rationale`,
        exactly as `Labeler.label` only ever names a fixed membership it's handed (mirrors that
        method's cache-hit / fallback / retry-on-fallback shape line for line)."""
        key = _bundle_key(bundle)
        cached = self.cache.get(key)
        if cached is not None and cached.get("source") == "llm":
            return ThemeLabel(label=cached["label"], rationale=cached["rationale"])

        subjects = "\n".join(f"- {a.subject}" for a in sorted(bundle.atoms, key=lambda a: a.commit_sha))
        prompt = (
            "Name the theme these commits share, in a semantic version-control tool.\n"
            "label: 2-5 words, Title Case, concrete.\n"
            "rationale: ONE factual sentence naming what changed. Do not start with 'These'.\n\n"
            f"Scope: {bundle.scope}\n"
            f"Commit subjects:\n{subjects}\n"
        )
        try:
            out, source = self._request(prompt, ThemeLabel), "llm"
        except Exception:
            out, source = _fallback_bundle_label(bundle), "fallback"
        self.cache[key] = {**out.model_dump(), "source": source}
        return out

    def group_scopeless(self, atoms: list[IntentAtom]) -> list[ThemeGroup]:
        """Coalesce scope-less atoms into named themes. Unlike `label_bundle`, membership here
        *is* LLM-decided (this is the "coalescer" half of KTD4) -- so every returned `atom_shas`
        is validated as a subset of the shas actually shown, and any hallucinated sha is dropped
        before persistence, never trusted.

        Processes all atoms in bounded, chronologically-ordered chunks of `MAX_ATOMS` (R8/KTD5)
        rather than truncating to the first `MAX_ATOMS` -- every scope-less atom gets a theme
        regardless of backlog size, one LLM call + one content-hash cache entry per chunk (an
        unchanged chunk on rebuild hits the cache; a chunk with a new atom does not). Populates
        `scopeless_chunk_key_by_sha` so `build_themes` can look up which chunk's cache entry
        produced a given returned group, without re-deriving the chunking here."""
        if not atoms:
            return []
        ordered = sorted(atoms, key=_atom_sort_key)
        chunks = [ordered[i:i + MAX_ATOMS] for i in range(0, len(ordered), MAX_ATOMS)]
        groups: list[ThemeGroup] = []
        for chunk in chunks:
            chunk_key = _scopeless_key(chunk)
            for atom in chunk:
                self.scopeless_chunk_key_by_sha[atom.commit_sha] = chunk_key
            groups.extend(self._group_scopeless_chunk(chunk, chunk_key))
        return groups

    def _group_scopeless_chunk(self, chunk: list[IntentAtom], key: str) -> list[ThemeGroup]:
        """One `MAX_ATOMS`-bounded chunk's LLM/cache call. `assigned` (R7) tracks which atom
        shas an earlier group (in the LLM's own returned order) already claimed, so a later
        group naming an already-claimed sha -- an overlapping LLM response -- drops it instead of
        letting the atom land in two persisted themes; first group wins."""
        cached = self.cache.get(key)
        valid_shas = frozenset(a.commit_sha for a in chunk)
        if cached is not None and cached.get("source") == "llm":
            return [
                ThemeGroup(label=g["label"], rationale=g["rationale"], atom_shas=g["atom_shas"])
                for g in cached["groups"]
            ]

        lines = "\n".join(f"{a.commit_sha[:8]} | {a.subject}" for a in chunk)
        prompt = (
            "These commits in a semantic version-control tool declared no conventional-commit "
            "scope. Group any that plausibly share one underlying theme (e.g. fixing the same "
            "bug, building the same feature) into named groups; leave a commit out of every "
            "group if it doesn't plausibly share a theme with any other.\n"
            "For each group: label (2-5 words, Title Case), rationale (one line), and atom_shas "
            "-- the exact 8-char sha prefixes from the list below that belong to this group "
            "(>=1). Never invent a sha not shown.\n\n"
            f"Commits (sha | subject):\n{lines}\n"
        )
        try:
            result, source = self._request(prompt, ThemeGroups), "llm"
            groups: list[ThemeGroup] = []
            assigned: set[str] = set()
            for g in result.groups:
                kept = [sha for sha in g.atom_shas if any(sha == a.commit_sha[:8] for a in chunk)]
                resolved = [
                    a.commit_sha for a in chunk if a.commit_sha[:8] in kept and a.commit_sha not in assigned
                ]
                if resolved:
                    groups.append(ThemeGroup(label=g.label, rationale=g.rationale, atom_shas=resolved))
                    assigned.update(resolved)
            for atom in chunk:
                if atom.commit_sha not in assigned:
                    groups.append(ThemeGroup(
                        label=(atom.subject or atom.commit_sha[:8])[:60],
                        rationale="Ungrouped commit.", atom_shas=[atom.commit_sha],
                    ))
                    assigned.add(atom.commit_sha)
        except Exception:
            groups, source = _fallback_scopeless_groups(chunk), "fallback"

        groups = filter_to_shown(groups, valid_shas, lambda g: g.atom_shas)
        self.cache[key] = {
            "source": source,
            "groups": [{"label": g.label, "rationale": g.rationale, "atom_shas": g.atom_shas} for g in groups],
        }
        return groups

    def save(self) -> None:
        state.save_json_if_changed(self._repo, "intent_cache", self.cache)

    def cost_line(self) -> str:
        est = self.tokens_in / 1e6 * 0.25 + self.tokens_out / 1e6 * 2.0
        return (
            f"themer: {self.calls} live calls, "
            f"{self.tokens_in} in + {self.tokens_out} out tokens (~${est:.4f}); "
            f"{len(self.cache)} cached"
        )


def build_themes(repo: str | Path) -> dict[str, dict]:
    """The overlay's one write path (mirrors `sgt.lens.map.build_map`): partition the store
    (rung 0/1, `sgt.intent.group`), name every scope bundle and coalesce every scope-less atom
    (rung 2, `IntentThemer`), mint each theme a content-addressed id, and persist to committed
    `.sgt/intent/themes.json`. Every atom lands in exactly one theme -- even an unbundled,
    unlabeled singleton gets a trivial one-atom theme -- so `themes.json` always covers the full
    atom partition (`sgt intent list` never needs a separate "uncategorized" case).

    Deliberately NOT auto-triggered by `sync`/`land`: unlike the feature tree (rebuilt in-memory,
    pre-commit, from data `sync` already holds), a real rebuild here needs `GitBinding.history()`
    -- which only reflects the *merged* history once the landing/merge commit actually exists.
    Calling this transactionally, before that commit, would either see the wrong (pre-merge)
    history or (called after) write a committed artifact into the working tree *after* the tree
    was already committed, leaving it permanently uncommitted. So `themes.json` is explicitly
    rebuilt on demand (`sgt intent build`, U7) -- like `sgt map` vs `map_view`, this write path is
    kept out of every read/sync path. Content-hash caching (U4) makes a post-sync rebuild cheap:
    atoms unchanged by the sync hit the cache; only genuinely new atoms cost a live call."""
    from sgt.core import opindex
    from sgt.core.lens import _load_declared
    from sgt.intent.group import atoms as _atoms
    from sgt.intent.group import scope_bundles

    repo = Path(repo)
    themer = IntentThemer(repo)
    all_atoms = _atoms(repo)
    all_ops = opindex.index_ops(repo)  # footprint/provenance only -- themes never reads .images
    declared = _load_declared(repo)
    bundles = scope_bundles(all_atoms, all_ops, declared)

    themes: dict[str, dict] = {}
    scopeless: list[IntentAtom] = []
    for bundle in bundles:
        if bundle.scope is None:
            scopeless.extend(bundle.atoms)
            continue
        label = themer.label_bundle(bundle)
        shas = frozenset(a.commit_sha for a in bundle.atoms)
        tid = theme_id_for(shas)
        cache_key = _bundle_key(bundle)
        source = themer.cache.get(cache_key, {}).get("source", "fallback")
        themes[tid] = {
            "label": label.label, "rationale": label.rationale,
            "atom_shas": sorted(shas), "source": source,
        }

    for group in themer.group_scopeless(scopeless):
        shas = frozenset(group.atom_shas)
        if not shas:
            continue
        tid = theme_id_for(shas)
        cache_key = themer.scopeless_chunk_key_by_sha[next(iter(shas))]
        source = themer.cache.get(cache_key, {}).get("source", "fallback")
        themes[tid] = {
            "label": group.label, "rationale": group.rationale,
            "atom_shas": sorted(shas), "source": source,
        }

    themer.save()
    state.save_json_if_changed(repo, "intent_themes", themes)
    return themes
