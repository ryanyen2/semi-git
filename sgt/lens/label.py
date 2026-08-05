"""LLM labeling for the feature tree (plan U12, R17): a pydantic-typed call naming a leaf/subsystem
from its members, cached by member-hash so a cluster whose membership is unchanged never re-pays
("dirty nodes only"). Promoted from `experiments/patch_clustering/label.py` (empirically validated
cost/quality on this repo's own history, see [[experiments-patch-clustering-findings]]), with two
changes: the client comes from `sgt.config.get_client` instead of ad hoc `.env` parsing (plan D6),
and every label has a deterministic offline fallback (`fallback_label`) so the tree never depends
on network/API availability to exist -- only to be *named well*.

Cache entries are tagged `"source": "llm"` or `"source": "fallback"`. A cache hit only short-
circuits the `"llm"` case; a fallback-sourced entry is retried once its backoff window expires, so
a repo that starts offline gets real labels the moment a key becomes available, without re-paying
for anything that already got a real one.

That retry is *windowed*, not per-call (KTD-perf). Retrying every fallback entry on every read made
the labeler a network round-trip on the read path forever in exactly the repos that can least
afford it -- one with no credential, a stale key, or an endpoint that is simply down. Every
`sgt log --refresh` then re-paid the full timeout for every terse feature, which measured as a
multi-second "why is this so slow" on a five-save repo. So a fallback entry now records when it is
next worth retrying (`retry_after`, exponential in that entry's consecutive failures), and a
credential that cannot be built at all short-circuits the whole pass in-process rather than failing
once per batch. `relabel=True` (what `--rebuild` passes) clears both, so the user always has an
explicit "try again now" that does not depend on waiting out a backoff.
"""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from sgt import state
from sgt.config import get_client, get_model

MAX_MEMBERS = 24
MAX_SUBJECTS = 6
MAX_BATCH = 8  # cluster-naming requests per `responses.parse` call in `label_many`
MAX_WORKERS = 6  # concurrent batches in flight -- network-bound, bounded to be a considerate API citizen
# Backoff for retrying a fallback-labeled cluster. Doubles per consecutive failure on that entry so
# a permanently-broken credential costs one attempt per day instead of one per read, while a
# transient blip (proxy hiccup, rate limit) is retried within the same working session.
FALLBACK_RETRY_BASE_SECONDS = 900  # 15 min after the first failure
FALLBACK_RETRY_MAX_SECONDS = 86400  # capped at a day
TAU_LABEL = 0.5  # weighted-Jaccard drift budget for reusing a leaf's LLM label (plan §3.2). A
# feature keeps its cached name across a re-cluster while the current member set stays within this
# much drift of the set the label was GENERATED on -- not the previous snapshot: anchoring at the
# generation set bounds cumulative drift (the ship-of-Theseus lemma), and each relabel resets the
# anchor. Aligned with Greene θ: identity continuation and name continuation share a notion of
# "still the same thing". Swept in the harness (§5).


def _weighted_jaccard(a: set[str], b: set[str], weights: dict[str, float]) -> float:
    """``J_w(A,B) = Σ_{x∈A∩B} w(x) / Σ_{x∈A∪B} w(x)``, ``w(x)`` = op-touch count of symbol x
    (default 1 when absent) -- so the label follows the feature's center of historical mass, not
    its raw symbol count. Two empty sets are identical (1.0)."""
    union = a | b
    if not union:
        return 1.0
    den = sum(weights.get(x, 1.0) for x in union)
    if den <= 0:
        return 1.0
    return sum(weights.get(x, 1.0) for x in (a & b)) / den


class FeatureLabel(BaseModel):
    label: str  # 2-5 words, human-facing feature name
    rationale: str  # one line: what this group of code is for


class _BatchItem(BaseModel):
    index: int  # position in the batch this item answers, so a reordered/partial response still maps back
    label: str
    rationale: str


class _FeatureLabelBatch(BaseModel):
    items: list[_BatchItem]


def _key(members: list[str]) -> str:
    return hashlib.sha1("\x00".join(sorted(members)).encode()).hexdigest()[:12]


def _leaf_prompt(
    members: list[str], subjects: list[str] | None = None, kinds: str | None = None,
) -> str:
    names = [m.split("::", 1)[1] if "::" in m else m for m in sorted(members)[:MAX_MEMBERS]]
    files = sorted({m.split("::", 1)[0] for m in members})[:8]
    subj = (subjects or [])[:MAX_SUBJECTS]
    return (
        "Name the feature this group of code entities implements, in a semantic version-control "
        "tool. Use the commit intents below as key evidence for WHAT this code is for, weighed "
        "together with the entity and file names (the entities are the ground truth for what the "
        "code IS; the intents say what it was FOR).\n"
        "label: 2-4 words, Title Case, concrete. No filler words ('System', 'Feature', "
        "'Management', 'Semantic').\n"
        "rationale: ONE factual sentence naming what it does. Do not start with 'These'.\n\n"
        f"Files: {', '.join(files)}\n"
        f"Entities: {', '.join(names)}\n"
        + (f"Commit intents: {' | '.join(subj)}\n" if subj else "")
        + (f"Change activity: {kinds}\n" if kinds else "")
    )


def _super_prompt(child_labels: list[str], files: list[str]) -> str:
    return (
        "Several feature groups in a semantic version-control tool cluster into ONE subsystem. "
        "Name the subsystem.\n"
        "label: 2-4 words, Title Case, broader than any single child, concrete. No filler "
        "('System', 'Feature', 'Management', 'Semantic').\n"
        "rationale: ONE factual sentence naming what the subsystem spans. Do not start with 'These'.\n\n"
        f"Folders: {', '.join(files)}\n"
        f"Child features: {', '.join(child_labels)}\n"
    )


def _clean_symbol_name(member: str) -> str | None:
    """A human-facing name for a cluster member, or ``None`` when the member is an internal
    fold-ordering artifact no user would recognise. A member is a symbol id: ``file::qualname``,
    ``file::__residue__::x`` / ``file::__anchor__::x`` (verbatim byte-spans between named entities --
    real bytes, but not a name), or a bare ``file`` (a whole-file member, e.g. a doc)."""
    if "::" not in member:                          # bare file (doc/config) -> its basename
        return member.rsplit("/", 1)[-1] or None
    _, _, rest = member.partition("::")
    if "__residue__" in rest or "__anchor__" in rest:   # internal fold artifact: no user-facing name
        return None
    name = rest.split("::")[-1].replace("\x00", "").strip("_")
    return name or None


_DOC_EXT = (".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".json",
            ".lock", ".html", ".css")


def fallback_label(members: list[str]) -> FeatureLabel:
    """Deterministic, offline, free, and *readable* -- and it names the cluster's *kind*, not just
    its first files, so a docs cluster doesn't masquerade as a code feature:
      - real code symbols present  -> the leading symbol names ("get_client get_model load_env")
      - only whole-file doc/config -> "docs & config · <dir>" (a 91-file docs group shouldn't read
        as the single feature "README.md")
      - nothing but fold artifacts -> "<dir> (structural)", never a raw ``__residue__::`` id
    Never cached as a permanent answer -- callers tag it `"source": "fallback"` so a later call
    with a working client overwrites it with a real label."""
    from sgt.lens.cluster import _dominant_dir

    code_names: list[str] = []
    file_names: list[str] = []
    for m in sorted(members):
        if "::" in m:
            n = _clean_symbol_name(m)  # None for residue/anchor fold artifacts
            if n and n not in code_names:
                code_names.append(n)
        else:  # a bare whole-file member (a doc, config, or binary asset)
            base = m.rsplit("/", 1)[-1]
            if base and base not in file_names:
                file_names.append(base)
    dom_dir = _dominant_dir(members)
    if code_names:
        label = " ".join(code_names[:3])
    elif file_names:
        docish = all(f.lower().endswith(_DOC_EXT) for f in file_names)
        label = (f"docs & config · {dom_dir}" if dom_dir else "docs & config") if docish \
            else " ".join(file_names[:3])
    else:
        label = f"{dom_dir} (structural)" if dom_dir else "(structural)"
    return FeatureLabel(label=label[:60],
                        rationale=f"Auto-derived from {dom_dir or 'the repo'} (no LLM label available).")


class Labeler:
    def __init__(self, repo: str | Path = ".", *, relabel: bool = False) -> None:
        self._repo = repo
        self._client = None
        self.cache: dict = state.load_json(repo, "label_cache", default={})
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0
        self._lock = threading.Lock()  # guards cache writes + token counters across concurrent batches
        self._auth_warned = False
        # `--rebuild`'s explicit "name everything again": ignore both the fallback backoff and any
        # cached LLM label, so the user's escape hatch never depends on waiting out a window.
        self._relabel = relabel
        # Set once `get_client` itself fails (no credential at all). Nothing in this process can
        # succeed after that, so every remaining entry falls back without attempting a call --
        # the difference between one failure and one per batch on a keyless repo.
        self._client_unavailable = False

    def _note_failure(self, exc: Exception) -> None:
        """First time an LLM call fails on *credentials*, note it once on stderr -- worded so it
        fits every case without crying wolf: a missing key, a permanently-rejected one (the whole
        graph goes terse -- the trap that made it look broken), and a transient proxy hiccup under
        concurrent batches (a few features fall back, the rest are fine). It reports the affected
        calls, not "labeling disabled", and points at the fix only *if* the graph is broadly
        terse."""
        name, msg = type(exc).__name__.lower(), str(exc).lower()
        # "No LLM credential found" (`config.get_client`) is a plain RuntimeError, so match the
        # message too: without it the most common cause of terse names -- no key configured at all
        # -- was the one case that explained itself to nobody.
        is_auth = ("auth" in name or "permission" in name or "401" in msg
                   or ("invalid" in msg and "token" in msg) or "credential" in msg)
        if is_auth and not self._auth_warned:
            self._auth_warned = True
            import sys
            print(f"⚠ an LLM labeling call was rejected ({type(exc).__name__}); those features use "
                  "terse fallback names. If the whole graph is terse, the key is missing or stale — "
                  "set OPENAI_API_KEY (or ANTHROPIC_AUTH_TOKEN for a Claude model), then "
                  "`sgt log --rebuild`.", file=sys.stderr)

    @property
    def client(self):
        if self._client is None:
            self._client = get_client(self._repo)
        return self._client

    def _client_or_none(self):
        """The client, or `None` once we know this process cannot build one. `get_client` raises
        when no credential resolves, which is a *configuration* fact, not a per-call one: retrying
        it per batch turns one missing key into N identical failures and N terse warnings."""
        if self._client_unavailable:
            return None
        try:
            return self.client
        except Exception as e:  # noqa: BLE001 -- any construction failure means no labeling here
            self._client_unavailable = True
            self._note_failure(e)
            return None

    def _fallback_entry(self, key: str, members: list[str], now: float | None = None) -> dict:
        """A fallback cache entry carrying its own next-retry time. `attempts` counts consecutive
        failures for this key so the backoff grows, and is reset by any later successful label."""
        prior = self.cache.get(key) or {}
        attempts = int(prior.get("attempts", 0)) + 1 if prior.get("source") == "fallback" else 1
        delay = min(FALLBACK_RETRY_BASE_SECONDS * (2 ** (attempts - 1)), FALLBACK_RETRY_MAX_SECONDS)
        out = fallback_label(members)
        return {**out.model_dump(), "source": "fallback", "attempts": attempts,
                "retry_after": (now if now is not None else time.time()) + delay}

    def _request(self, prompt: str) -> FeatureLabel:
        # `reasoning={"effort": "low"}`: naming a cluster is one-shot structured extraction that
        # never needs a deep thinking trace, and this is the highest-volume LLM path (every leaf +
        # subsystem on the `sgt map` / `sgt graph --refresh` hot path). Omitting `reasoning=` defaults
        # a real reasoning model to "medium" -- a cost inversion here -- so we pin it low, matching
        # every other call site (`intent.theme`, `intent.theme_segment`, `repair.resolve`).
        r = self.client.responses.parse(
            model=get_model(self._repo), input=prompt, text_format=FeatureLabel,
            reasoning={"effort": "low"},
        )
        out = r.output_parsed
        if out is None:  # refusal / content-filter / length stop -> raise so `_resolve` falls back
            raise ValueError("empty label parse")
        with self._lock:
            self.calls += 1
            self.tokens_in += r.usage.input_tokens
            self.tokens_out += r.usage.output_tokens
        return out

    def _request_batch(self, prompts: list[str]) -> list[FeatureLabel | None]:
        """One `responses.parse` call naming `len(prompts)` independent clusters -- each prompt
        already carries its own full instructions (identical text to a solo `label`/`label_super`
        call), just answered together to save round-trips. Returns a list aligned to `prompts`
        by index; `None` where the model dropped or misindexed that slot (caller falls back)."""
        body = "\n\n".join(f"=== Group {i} ===\n{p}" for i, p in enumerate(prompts))
        combined = (
            f"Below are {len(prompts)} independent naming tasks, each already containing its own "
            "instructions. Answer each one separately -- do not let one group's context bleed into "
            "another's. Return exactly one item per group, with `index` matching the group number.\n\n"
            + body
        )
        r = self.client.responses.parse(
            model=get_model(self._repo), input=combined, text_format=_FeatureLabelBatch,
            reasoning={"effort": "low"},
        )
        with self._lock:
            self.calls += 1
            self.tokens_in += r.usage.input_tokens
            self.tokens_out += r.usage.output_tokens
        out: list[FeatureLabel | None] = [None] * len(prompts)
        for item in r.output_parsed.items:
            if 0 <= item.index < len(prompts):
                out[item.index] = FeatureLabel(label=item.label, rationale=item.rationale)
        return out

    def _resolve(self, key: str, prompt: str, members: list[str]) -> FeatureLabel:
        cached = self.cache.get(key)
        if not self._relabel and cached is not None:
            if cached.get("source") == "llm":
                return FeatureLabel(label=cached["label"], rationale=cached["rationale"])
            if cached.get("source") == "fallback" and time.time() < cached.get("retry_after", 0):
                return FeatureLabel(label=cached["label"], rationale=cached["rationale"])
        if self._client_or_none() is None:
            entry = self._fallback_entry(key, members)
            self.cache[key] = entry
            return FeatureLabel(label=entry["label"], rationale=entry["rationale"])
        try:
            out = self._request(prompt)
        except Exception as e:
            self._note_failure(e)
            entry = self._fallback_entry(key, members)
            self.cache[key] = entry
            return FeatureLabel(label=entry["label"], rationale=entry["rationale"])
        self.cache[key] = {**out.model_dump(), "source": "llm"}
        return out

    def label(
        self, members: list[str], subjects: list[str] | None = None, kinds: str | None = None,
    ) -> FeatureLabel:
        return self._resolve(_key(members), _leaf_prompt(members, subjects, kinds), members)

    def label_super(self, child_labels: list[str], files: list[str]) -> FeatureLabel:
        """Name a subsystem from the feature labels of its children (one level up the tree)."""
        key = _key(["\x01super", *child_labels, *files])
        return self._resolve(key, _super_prompt(child_labels, files), [*child_labels, *files])

    def leaf_request(
        self, feature_id: str, members: list[str], weights: dict[str, float] | None = None,
        subjects: list[str] | None = None, kinds: str | None = None,
    ) -> tuple[str, str, list[str], dict[str, float]]:
        """``(key, prompt, members, weights)`` for `label_many`. The cache key is the FEATURE ID
        (leaf node ids are feature ids at `label_tree` time), so an unchanged feature keeps its
        cache entry across a re-cluster even when a member is added/dropped -- reuse is graded by
        weighted-Jaccard drift from the generation-time member set (§3.2), not exact member-set
        match. `weights` (op-touch counts) grades that drift; a dict (even empty) marks this a leaf
        entry, ``None`` becomes unit weights. `subjects`/`kinds` enrich the prompt."""
        return feature_id, _leaf_prompt(members, subjects, kinds), members, dict(weights or {})

    def super_request(
        self, child_labels: list[str], files: list[str],
    ) -> tuple[str, str, list[str], None]:
        """``(key, prompt, members, None)`` for `label_many` -- content-keyed exact reuse, mirrors
        `label_super()`. The ``None`` weights slot marks a super entry (no graded drift budget):
        stabilizing leaf labels stabilizes the super cascade for free (super keys are child labels)."""
        key = _key(["\x01super", *child_labels, *files])
        return key, _super_prompt(child_labels, files), [*child_labels, *files], None

    def _cache_lookup(
        self, key: str, members: list[str], weights: dict[str, float] | None,
    ) -> FeatureLabel | None:
        """The reusable cached label for one `label_many` entry, or ``None`` to (re)compute.
        A `fallback`-sourced entry is reused until its `retry_after` window expires, so a broken or
        absent credential costs one attempt per backoff period rather than one per read. A super
        entry (`weights is None`) reuses on exact-key match. A leaf entry reuses iff the current
        member set is within `TAU_LABEL` weighted-Jaccard drift of the generation-time set the label
        was earned on. On a leaf miss with no id-keyed entry, a legacy member-hash entry is adopted
        as this feature's generation point without an LLM call (lazy re-key). Mutates the cache
        only on that adoption; called single-threaded before the batch pool starts."""
        if self._relabel:
            return None  # `--rebuild`: name everything again, cache and backoff both ignored
        cached = self.cache.get(key)
        if (cached is not None and cached.get("source") == "fallback"
                and time.time() < cached.get("retry_after", 0)):
            return FeatureLabel(label=cached["label"], rationale=cached["rationale"])
        if cached is not None and cached.get("source") == "llm":
            if weights is None:  # super: content-keyed, exact match is identity
                return FeatureLabel(label=cached["label"], rationale=cached["rationale"])
            gen = cached.get("gen_members")
            if gen is None or _weighted_jaccard(set(members), set(gen), weights) >= TAU_LABEL:
                return FeatureLabel(label=cached["label"], rationale=cached["rationale"])
            return None  # drifted past the budget -> force a relabel (resets the anchor)
        if cached is None and weights is not None:  # leaf miss: try the pre-graded member-hash key
            legacy = self.cache.get(_key(members))
            if legacy is not None and legacy.get("source") == "llm":
                self.cache[key] = {
                    "label": legacy["label"], "rationale": legacy["rationale"], "source": "llm",
                    "gen_members": sorted(members), "member_hash": _key(members),
                }
                return FeatureLabel(label=legacy["label"], rationale=legacy["rationale"])
        return None

    def label_many(
        self, entries: list[tuple[str, str, list[str], dict[str, float] | None]],
    ) -> list[FeatureLabel]:
        """Resolve many ``(key, prompt, members, weights)`` entries -- built by `leaf_request`/
        `super_request` -- batching cache misses (`MAX_BATCH` per `responses.parse` call) and
        running the batches concurrently (`ThreadPoolExecutor`; network-bound, releases the GIL).
        Cache hits are served locally with zero network calls; leaf reuse is graded (`_cache_lookup`,
        §3.2) so a small membership drift keeps the name. A batch item the model drops or misindexes
        gets the same deterministic fallback a solo call would use -- one bad item never fails the
        whole batch."""
        results: list[FeatureLabel | None] = [None] * len(entries)
        misses: list[int] = []
        for i, (key, _prompt, members, weights) in enumerate(entries):
            hit = self._cache_lookup(key, members, weights)
            if hit is not None:
                results[i] = hit
            else:
                misses.append(i)
        if not misses:
            return results  # type: ignore[return-value]

        def _record_fallbacks(batch_idx: list[int]) -> None:
            """Fall every entry in `batch_idx` back, each with its own backoff, and stamp the leaf
            drift anchor exactly as a successful label would -- so a later retry compares against
            the same generation set rather than treating the feature as brand new."""
            for global_i in batch_idx:
                key, _prompt, members, weights = entries[global_i]
                with self._lock:
                    entry = self._fallback_entry(key, members)
                    if weights is not None:
                        entry["gen_members"] = sorted(members)
                        entry["member_hash"] = _key(members)
                    self.cache[key] = entry
                results[global_i] = FeatureLabel(label=entry["label"], rationale=entry["rationale"])

        # No credential resolves at all: skip the pool entirely rather than paying one construction
        # failure per batch. Every miss falls back with a backoff stamped, so the next read is local.
        if self._client_or_none() is None:
            _record_fallbacks(misses)
            return results  # type: ignore[return-value]

        batches = [misses[i:i + MAX_BATCH] for i in range(0, len(misses), MAX_BATCH)]

        def _run_batch(batch_idx: list[int]) -> None:
            prompts = [entries[i][1] for i in batch_idx]
            try:
                batch_out = self._request_batch(prompts)
            except Exception as e:
                self._note_failure(e)
                _record_fallbacks(batch_idx)
                return
            for local_i, global_i in enumerate(batch_idx):
                key, _prompt, members, weights = entries[global_i]
                out = batch_out[local_i]
                if out is None:  # the model dropped/misindexed this slot -- fall back just this one
                    with self._lock:
                        entry = self._fallback_entry(key, members)
                        if weights is not None:
                            entry["gen_members"] = sorted(members)
                            entry["member_hash"] = _key(members)
                        self.cache[key] = entry
                    results[global_i] = FeatureLabel(label=entry["label"], rationale=entry["rationale"])
                    continue
                entry = {**out.model_dump(), "source": "llm"}
                if weights is not None:  # leaf: anchor the drift budget at this generation
                    entry["gen_members"] = sorted(members)
                    entry["member_hash"] = _key(members)
                with self._lock:
                    self.cache[key] = entry
                results[global_i] = out

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(batches))) as pool:
            list(pool.map(_run_batch, batches))

        return results  # type: ignore[return-value]

    def save(self) -> None:
        """Skips the write when the cache is byte-identical to what's already on disk (see
        `state.save_json_if_changed`) -- a build with zero new/changed labels shouldn't bump
        `label_cache.json`'s mtime and retrigger a client's file watcher."""
        state.save_json_if_changed(self._repo, "label_cache", self.cache)

    def cost_line(self) -> str:
        est = self.tokens_in / 1e6 * 0.25 + self.tokens_out / 1e6 * 2.0  # ~ballpark $/Mtok
        return (
            f"labeler: {self.calls} live calls, "
            f"{self.tokens_in} in + {self.tokens_out} out tokens (~${est:.4f}); "
            f"{len(self.cache)} cached"
        )
