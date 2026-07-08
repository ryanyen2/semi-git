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
import json
from pathlib import Path

from pydantic import BaseModel

from sgt.config import get_client

MODEL = "gpt-5.4-mini"
EFFORT = "low"
MAX_MEMBERS = 24
MAX_SUBJECTS = 6


class FeatureLabel(BaseModel):
    label: str  # 2-5 words, human-facing feature name
    rationale: str  # one line: what this group of code is for


def _key(members: list[str]) -> str:
    return hashlib.sha1("\x00".join(sorted(members)).encode()).hexdigest()[:12]


def _fallback_label(members: list[str]) -> FeatureLabel:
    """Deterministic, offline, free: the dominant directory plus the leading member names. Never
    cached as a permanent answer -- callers tag it `"source": "fallback"` so a later call with a
    working client overwrites it with a real label."""
    from sgt.lens.cluster import _dominant_dir

    names = [m.split("::", 1)[1] if "::" in m else m for m in sorted(members)[:3]]
    dom_dir = _dominant_dir(members)
    label = " ".join(n.strip("_") for n in names)[:60] or dom_dir
    return FeatureLabel(label=label, rationale=f"Auto-derived from {dom_dir} (no LLM label available).")


def _cache_path(repo: str | Path) -> Path:
    return Path(repo) / ".sgt" / "local" / "label_cache.json"


class Labeler:
    def __init__(self, repo: str | Path = ".") -> None:
        self._repo = repo
        self._client = None
        self._cache_path = _cache_path(repo)
        self.cache: dict = (
            json.loads(self._cache_path.read_text(encoding="utf-8")) if self._cache_path.is_file() else {}
        )
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0

    @property
    def client(self):
        if self._client is None:
            self._client = get_client(self._repo)
        return self._client

    def _request(self, prompt: str) -> FeatureLabel:
        r = self.client.responses.parse(
            model=MODEL, input=prompt, text_format=FeatureLabel, reasoning={"effort": EFFORT},
        )
        self.calls += 1
        self.tokens_in += r.usage.input_tokens
        self.tokens_out += r.usage.output_tokens
        return r.output_parsed

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

    def label(self, members: list[str], subjects: list[str] | None = None) -> FeatureLabel:
        key = _key(members)
        names = [m.split("::", 1)[1] if "::" in m else m for m in sorted(members)[:MAX_MEMBERS]]
        files = sorted({m.split("::", 1)[0] for m in members})[:8]
        subj = (subjects or [])[:MAX_SUBJECTS]
        prompt = (
            "Name the feature this group of code entities implements, in a semantic version-control "
            "tool.\n"
            "label: 2-4 words, Title Case, concrete. No filler words ('System', 'Feature', "
            "'Management', 'Semantic').\n"
            "rationale: ONE factual sentence naming what it does. Do not start with 'These'.\n\n"
            f"Files: {', '.join(files)}\n"
            f"Entities: {', '.join(names)}\n"
            + (f"Commit intents: {' | '.join(subj)}\n" if subj else "")
        )
        return self._resolve(key, prompt, members)

    def label_super(self, child_labels: list[str], files: list[str]) -> FeatureLabel:
        """Name a subsystem from the feature labels of its children (one level up the tree)."""
        key = _key(["\x01super", *child_labels, *files])
        prompt = (
            "Several feature groups in a semantic version-control tool cluster into ONE subsystem. "
            "Name the subsystem.\n"
            "label: 2-4 words, Title Case, broader than any single child, concrete. No filler "
            "('System', 'Feature', 'Management', 'Semantic').\n"
            "rationale: ONE factual sentence naming what the subsystem spans. Do not start with 'These'.\n\n"
            f"Folders: {', '.join(files)}\n"
            f"Child features: {', '.join(child_labels)}\n"
        )
        return self._resolve(key, prompt, [*child_labels, *files])

    def save(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self.cache, indent=2), encoding="utf-8")

    def cost_line(self) -> str:
        est = self.tokens_in / 1e6 * 0.25 + self.tokens_out / 1e6 * 2.0  # ~ballpark $/Mtok
        return (
            f"labeler: {self.calls} live calls, "
            f"{self.tokens_in} in + {self.tokens_out} out tokens (~${est:.4f}); "
            f"{len(self.cache)} cached"
        )
