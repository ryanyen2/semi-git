"""Find the thing you can describe but cannot name.

Every other way into this graph needs you to already know an identifier: an
op-id, a `file::symbol`, a feature id, an exact label. The one rung that
accepted a phrase was `sgt feature select`'s fallback, a `difflib` ratio against
feature labels, which cannot answer its own documented example -- "the thing
that formats dates" scores 0.28 against `Time Slots` and returns nothing.

So this indexes what the graph already knows in words: a feature's label and the
sentence the labeller wrote about it, the message on every save, and the symbol
names themselves. A query is embedded and compared against that.

Two properties it has to keep. It is **report-only** -- a search that could
change anything would be a search nobody dares run. And it degrades rather than
fails: with no key, no network, or no index, the same call falls back to token
overlap over the same corpus. Worse answers, never an error, because the search
box is the first thing someone reaches for when they are lost, and that is the
worst possible moment to hand back a stack trace.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

INDEX_PATH = Path(".sgt") / "local" / "search_index.json"

# Small and dimension-reduced. The index travels inside a repo, and 1536 floats
# per entry turns a 300-entry index into five megabytes of JSON; 256 keeps it
# under one, and the ranking difference over a corpus this small is not
# something a person could notice.
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 256
BATCH = 96


@dataclass(frozen=True)
class Hit:
    kind: str  # "feature" | "save" | "symbol"
    id: str
    label: str
    detail: str
    score: float
    # The feature a hit sits on, so a caller has somewhere to take the reader.
    # A symbol on its own is not a place you can be shown in a graph of features.
    feature: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id, "label": self.label, "detail": self.detail,
                "feature": self.feature, "score": round(self.score, 4)}


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------

def _symbol_words(symbol: str) -> str:
    """`coursecraft/slots.py::parse_slot` -> "coursecraft slots parse slot".

    Identifiers are how the code says what it does, and they are the part of the
    corpus a lexical fallback can still work with when there is no key.
    """
    name = symbol.replace("::", " ").replace("/", " ").replace(".py", " ")
    name = re.sub(r"__\w+__", " ", name)  # __residue__, __anchor__ and friends
    name = re.sub(r"[_\-.]+", " ", name)
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    return " ".join(name.split())


def corpus(repo: str | Path) -> list[dict]:
    """Everything worth finding, as {kind, id, label, detail, text}."""
    from sgt.api import history_view, map_view

    entries: list[dict] = []
    seen_symbols: set[str] = set()

    for node in map_view(repo).get("nodes") or []:
        # Leaf features only. A subsystem's `why` mentions everything under it,
        # so "waitlist" matched the whole-repo root above the waitlist features
        # themselves -- and a subsystem is not something any verb accepts, so
        # the top hit would have been the one result you can do nothing with.
        if node.get("kind") != "feature":
            continue
        members = [m for m in (node.get("members") or []) if "__" not in m]
        label = str(node.get("label") or node.get("id") or "")
        why = str(node.get("why") or "")
        entries.append({
            "kind": "feature",
            "id": str(node.get("id") or ""),
            "feature": str(node.get("id") or ""),
            "label": label,
            "detail": why[:200] or f"{len(members)} symbol(s)",
            # The label and the sentence about it carry the meaning; the symbol
            # names carry the vocabulary someone is likely to search in.
            "text": " ".join([label, why, " ".join(_symbol_words(m) for m in members[:40])]),
        })
        for m in members:
            if m in seen_symbols:
                continue
            seen_symbols.add(m)
            entries.append({
                "kind": "symbol", "id": m, "label": m, "feature": str(node.get("id") or ""),
                "detail": f"in {label}", "text": _symbol_words(m),
            })

    for commit in history_view(repo, full=True).get("commits") or []:
        # sgt's own commits are bookkeeping, and their subjects are 64-hex.
        if commit.get("bookkeeping"):
            continue
        subject = str(commit.get("subject") or "")
        if not subject:
            continue
        entries.append({
            "kind": "save",
            "id": str(commit.get("sha") or "")[:8],
            "feature": "",
            "label": subject,
            "detail": f"save {str(commit.get('sha') or '')[:8]}",
            "text": subject,
        })

    return entries


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _embed(repo: str | Path, texts: list[str]) -> list[list[float]] | None:
    """Embed in batches, or None if there is no usable credential/endpoint."""
    from sgt.config import get_client

    try:
        client = get_client(repo)
    except Exception:
        return None

    out: list[list[float]] = []
    for start in range(0, len(texts), BATCH):
        chunk = [t or " " for t in texts[start:start + BATCH]]
        try:
            reply = client.embeddings.create(model=EMBED_MODEL, input=chunk, dimensions=EMBED_DIMS)
        except Exception:
            return None
        out.extend([round(x, 4) for x in item.embedding] for item in reply.data)
    return out


def build_index(repo: str | Path) -> dict:
    """Embed the corpus and write it beside the store. Returns a small report."""
    repo = Path(repo)
    entries = corpus(repo)
    vectors = _embed(repo, [e["text"] for e in entries])
    index = {
        "model": EMBED_MODEL if vectors else None,
        "dims": EMBED_DIMS if vectors else 0,
        "entries": [
            {k: e[k] for k in ("kind", "id", "label", "detail", "text", "feature")} | (
                {"vec": vectors[i]} if vectors else {}
            )
            for i, e in enumerate(entries)
        ],
    }
    path = repo / INDEX_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index), encoding="utf-8")
    return {"entries": len(entries), "embedded": bool(vectors), "path": str(path)}


def _load_index(repo: Path) -> dict | None:
    try:
        return json.loads((repo / INDEX_PATH).read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


_STOP = {"the", "a", "an", "that", "this", "it", "of", "to", "for", "in", "on",
         "and", "or", "is", "was", "thing", "what", "which", "where", "with"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 2}


def _lexical(entries: list[dict], query: str, k: int) -> list[Hit]:
    """Token overlap, normalized by query length. The offline answer."""
    q = _tokens(query)
    if not q:
        return []
    scored: list[Hit] = []
    for e in entries:
        overlap = len(q & _tokens(e.get("text") or ""))
        if not overlap:
            continue
        scored.append(Hit(e["kind"], e["id"], e["label"], e["detail"], overlap / len(q),
                          e.get("feature", "")))
    scored.sort(key=lambda h: -h.score)
    return scored[:k]


def search(repo: str | Path, query: str, k: int = 8, *, refresh: bool = False) -> dict:
    """Rank what this repo holds against a phrase.

    Never raises for want of a key, a network or an index: each of those falls
    through to the lexical rung, and the result says which rung answered so a
    caller can tell a weak answer from a confident one.
    """
    repo = Path(repo)
    query = (query or "").strip()
    if not query:
        return {"ok": False, "message": "empty query", "mode": "none", "hits": []}

    index = None if refresh else _load_index(repo)
    if index is None:
        build_index(repo)
        index = _load_index(repo) or {"entries": []}

    entries = index.get("entries") or []
    if not entries:
        return {"ok": False, "message": "nothing indexed yet", "mode": "none", "hits": []}

    vectors = [e for e in entries if e.get("vec")]
    if vectors:
        qv = _embed(repo, [query])
        if qv:
            hits = sorted(
                (Hit(e["kind"], e["id"], e["label"], e["detail"], _cosine(qv[0], e["vec"]),
                     e.get("feature", ""))
                 for e in vectors),
                key=lambda h: -h.score,
            )[:k]
            return {"ok": True, "mode": "semantic", "query": query,
                    "hits": [h.as_dict() for h in hits]}

    hits = _lexical(entries, query, k)
    return {"ok": bool(hits), "mode": "lexical", "query": query,
            "message": "" if hits else "nothing matched",
            "hits": [h.as_dict() for h in hits]}
