"""LLM labeling for the feature tree (plan U12, R17): a pydantic-typed call naming a leaf/subsystem
from its members, cached by member-hash so a cluster whose membership is unchanged never re-pays
("dirty nodes only"). Promoted from `experiments/patch_clustering/label.py` (empirically validated
cost/quality on this repo's own history, see [[experiments-patch-clustering-findings]]), with two
changes: the client comes from `sgt.config.get_client` instead of ad hoc `.env` parsing (plan D6),
and every label has a deterministic offline fallback (`_fallback_label`) so the tree never depends
on network/API availability to exist -- only to be *named well*.

Cache entries are tagged `"source": "llm"` or `"source": "fallback"`. A cache hit only short-
circuits the `"llm"` case; a fallback-sourced entry is retried on the next call that has a working
client, so a repo that starts offline gets real labels the moment a key becomes available, without
re-paying for anything that already got a real one.
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from sgt import state
from sgt.config import get_client, get_model

EFFORT = "low"
MAX_MEMBERS = 24
MAX_SUBJECTS = 6
MAX_BATCH = 8  # cluster-naming requests per `responses.parse` call in `label_many`
MAX_WORKERS = 6  # concurrent batches in flight -- network-bound, bounded to be a considerate API citizen


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


def _fallback_label(members: list[str]) -> FeatureLabel:
    """Deterministic, offline, free, and *readable*: the leading real symbol names, or -- when a
    cluster is nothing but fold artifacts (residue/anchor) -- the dominant directory tagged
    ``(structural)`` so the lane reads as glue-between-symbols, never a raw ``__residue__::`` id.
    Never cached as a permanent answer -- callers tag it `"source": "fallback"` so a later call
    with a working client overwrites it with a real label."""
    from sgt.lens.cluster import _dominant_dir

    names: list[str] = []
    for m in sorted(members):
        n = _clean_symbol_name(m)
        if n and n not in names:
            names.append(n)
        if len(names) >= 3:
            break
    dom_dir = _dominant_dir(members)
    if names:
        label = " ".join(names)[:60]
    else:
        label = f"{dom_dir} (structural)" if dom_dir else "(structural)"
    return FeatureLabel(label=label[:60],
                        rationale=f"Auto-derived from {dom_dir or 'the repo'} (no LLM label available).")


class Labeler:
    def __init__(self, repo: str | Path = ".") -> None:
        self._repo = repo
        self._client = None
        self.cache: dict = state.load_json(repo, "label_cache", default={})
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0
        self._lock = threading.Lock()  # guards cache writes + token counters across concurrent batches

    @property
    def client(self):
        if self._client is None:
            self._client = get_client(self._repo)
        return self._client

    def _request(self, prompt: str) -> FeatureLabel:
        r = self.client.responses.parse(
            model=get_model(self._repo), input=prompt, text_format=FeatureLabel,
            reasoning={"effort": EFFORT},
        )
        with self._lock:
            self.calls += 1
            self.tokens_in += r.usage.input_tokens
            self.tokens_out += r.usage.output_tokens
        return r.output_parsed

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
            reasoning={"effort": EFFORT},
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
        if cached is not None and cached.get("source") == "llm":
            return FeatureLabel(label=cached["label"], rationale=cached["rationale"])
        try:
            out, source = self._request(prompt), "llm"
        except Exception:
            out, source = _fallback_label(members), "fallback"
        self.cache[key] = {**out.model_dump(), "source": source}
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
        self, members: list[str], subjects: list[str] | None = None, kinds: str | None = None,
    ) -> tuple[str, str, list[str]]:
        """``(key, prompt, fallback_members)`` for `label_many` -- the exact key/prompt `label()`
        would use for a solo call, so a batched result caches identically to an unbatched one.
        The cache key is the member-set hash only; `subjects`/`kinds` enrich the prompt but a
        recluster (new member-set) is what busts the cache to pick up an enriched label."""
        return _key(members), _leaf_prompt(members, subjects, kinds), members

    def super_request(self, child_labels: list[str], files: list[str]) -> tuple[str, str, list[str]]:
        """``(key, prompt, fallback_members)`` for `label_many` -- mirrors `label_super()`."""
        key = _key(["\x01super", *child_labels, *files])
        return key, _super_prompt(child_labels, files), [*child_labels, *files]

    def label_many(self, entries: list[tuple[str, str, list[str]]]) -> list[FeatureLabel]:
        """Resolve many ``(key, prompt, fallback_members)`` triples -- built by `leaf_request`/
        `super_request` -- batching cache misses (`MAX_BATCH` per `responses.parse` call) and
        running the batches concurrently (`ThreadPoolExecutor`; network-bound, releases the GIL).
        Cache hits are served locally with zero network calls, same as `label`/`label_super`. A
        batch item the model drops or misindexes gets the same deterministic fallback a solo call
        would use on failure -- one bad item never fails the whole batch."""
        results: list[FeatureLabel | None] = [None] * len(entries)
        misses: list[int] = []
        for i, (key, _prompt, _members) in enumerate(entries):
            cached = self.cache.get(key)
            if cached is not None and cached.get("source") == "llm":
                results[i] = FeatureLabel(label=cached["label"], rationale=cached["rationale"])
            else:
                misses.append(i)
        if not misses:
            return results  # type: ignore[return-value]

        batches = [misses[i:i + MAX_BATCH] for i in range(0, len(misses), MAX_BATCH)]

        def _run_batch(batch_idx: list[int]) -> None:
            prompts = [entries[i][1] for i in batch_idx]
            try:
                batch_out = self._request_batch(prompts)
            except Exception:
                batch_out = [None] * len(prompts)
            for local_i, global_i in enumerate(batch_idx):
                key, _prompt, members = entries[global_i]
                out = batch_out[local_i]
                source = "llm"
                if out is None:
                    out, source = _fallback_label(members), "fallback"
                with self._lock:
                    self.cache[key] = {**out.model_dump(), "source": source}
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
