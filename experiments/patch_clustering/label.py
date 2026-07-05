"""Label clusters with gpt-5.4-mini — typed output, cost-tracked, cached.

Labeling is the one paid step, so it earns its keep only where determinism can't decide:
naming a group and, via the commit subjects that touched it, binding the intent that
sparse co-change failed to reveal. Everything upstream is free/deterministic.

Cost discipline: cache keyed by the member-set hash (a cluster whose membership is
unchanged never re-pays), reasoning effort ``low``, member/subject lists bounded, and
running-token accounting printed. Uses the openai SDK's typed ``responses.parse`` — no
extra dependency (verified against gpt-5.4-mini: efforts are none|low|medium|high|xhigh).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel

_HERE = Path(__file__).resolve().parent
_CACHE = _HERE / "out" / "label_cache.json"
MODEL = "gpt-5.4-mini"
EFFORT = "low"
MAX_MEMBERS = 24
MAX_SUBJECTS = 6


class FeatureLabel(BaseModel):
    label: str  # 2-5 words, human-facing feature name
    rationale: str  # one line: what this group of code is for


def _key(members: list[str]) -> str:
    return hashlib.sha1("\x00".join(sorted(members)).encode()).hexdigest()[:12]


def _load_key() -> str:
    for line in (_HERE.parent.parent / ".env").read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if line.startswith("OPENAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("OPENAI_API_KEY not found in .env")


class Labeler:
    def __init__(self) -> None:
        self._client: OpenAI | None = None
        self.cache: dict = json.loads(_CACHE.read_text()) if _CACHE.exists() else {}
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(api_key=_load_key())
        return self._client

    def label(self, members: list[str], subjects: list[str] | None = None) -> FeatureLabel:
        key = _key(members)
        if key in self.cache:
            return FeatureLabel(**self.cache[key])

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
        r = self.client.responses.parse(
            model=MODEL, input=prompt, text_format=FeatureLabel,
            reasoning={"effort": EFFORT},
        )
        self.calls += 1
        self.tokens_in += r.usage.input_tokens
        self.tokens_out += r.usage.output_tokens
        out = r.output_parsed
        self.cache[key] = out.model_dump()
        return out

    def label_super(self, child_labels: list[str], files: list[str]) -> FeatureLabel:
        """Name a subsystem from the feature labels of its children (one level up the tree)."""
        key = _key(["\x01super", *child_labels, *files])
        if key in self.cache:
            return FeatureLabel(**self.cache[key])
        prompt = (
            "Several feature groups in a semantic version-control tool cluster into ONE subsystem. "
            "Name the subsystem.\n"
            "label: 2-4 words, Title Case, broader than any single child, concrete. No filler "
            "('System', 'Feature', 'Management', 'Semantic').\n"
            "rationale: ONE factual sentence naming what the subsystem spans. Do not start with 'These'.\n\n"
            f"Folders: {', '.join(files)}\n"
            f"Child features: {', '.join(child_labels)}\n"
        )
        r = self.client.responses.parse(
            model=MODEL, input=prompt, text_format=FeatureLabel,
            reasoning={"effort": EFFORT},
        )
        self.calls += 1
        self.tokens_in += r.usage.input_tokens
        self.tokens_out += r.usage.output_tokens
        out = r.output_parsed
        self.cache[key] = out.model_dump()
        return out

    def save(self) -> None:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(self.cache, indent=2), encoding="utf-8")

    def cost_line(self) -> str:
        # gpt-5.4-mini is cheap; report tokens and a rough cost estimate.
        est = self.tokens_in / 1e6 * 0.25 + self.tokens_out / 1e6 * 2.0  # ~ballpark $/Mtok
        return (f"labeler: {self.calls} live calls, "
                f"{self.tokens_in} in + {self.tokens_out} out tokens (~${est:.4f}); "
                f"{len(self.cache)} cached")
