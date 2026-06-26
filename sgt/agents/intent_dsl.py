"""The intent DSL — a controlled-NL front end that fills the *same* planner schema.

A user states intent in one of two registers, both ending at the same structured ``SubTask``
(``orchestrate/constraint.py``), so everything downstream (``add_plan``, fold-on-revise,
decisions) is unchanged — this is a new front end, not a new backend (the "one schema" invariant):

* **Canonical DSL** — an uppercase-verb command that ``parse`` turns into a ``ParsedIntent``
  deterministically and offline (no API key). The uppercase verb is the opt-in: ``ADD foo`` is
  canonical, ``add foo`` is freeform prose. Power users get exact, key-free control.
* **Freeform** — anything else. ``normalize`` (the one LLM touch here) renders it into a *list* of
  canonical statements — one per capability, so decomposition is preserved — which the caller
  echoes for confirmation and then parses. Freeform users learn the grammar by seeing their own
  intent rendered into it; the loop still degrades to the legacy planner with no key.

Grammar (keywords matched case-sensitively for the leading verb, case-insensitively for connectors;
``<names>`` is a comma/``and``-separated list of identifiers):

    ADD     <names> [USING <names>] [BECAUSE <reason>]      new capability; provides=names, needs=USING
    EXTEND  <lane> (TO|WITH) <behavior> [BECAUSE <reason>]  revise an existing lane
    REPLACE <name> WITH <approach> [BECAUSE <reason>]       revise + Alternative{option:name, why:reason}
    REMOVE  <names> [FROM <lane>] [BECAUSE <reason>]        revise that removes def(s)

See docs/design/2026-06-25-plan-editing-and-intent-dsl.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# The four verbs that open a canonical statement. Uppercase is the deliberate opt-in: it makes the
# canonical form a visually distinct command and keeps ordinary prose ("add a login form") freeform.
VERBS = ("ADD", "EXTEND", "REPLACE", "REMOVE")


@dataclass
class ParsedIntent:
    """A canonical statement, parsed. ``intent`` is the reconstructed coding request the planner
    schema wants; ``provides``/``needs`` are the declared interface; ``target`` is the lane/name a
    revise verb edits; ``context`` and ``alternative`` carry ``BECAUSE`` rationale."""

    verb: str
    intent: str
    slug: str | None = None
    provides: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    target: str | None = None  # EXTEND/REMOVE: the lane; REPLACE: the name whose lane is revised
    context: str | None = None  # BECAUSE on ADD/EXTEND/REMOVE — the situation, not a rejected option
    alternative: tuple[str, str] | None = None  # REPLACE: (option_rejected, why) — user-asserted
    canonical: str = ""  # the normalized rendering (echo/confirm)

    @property
    def is_revise(self) -> bool:
        """EXTEND/REPLACE/REMOVE edit an existing lane; ADD starts a new one."""
        return self.verb in ("EXTEND", "REPLACE", "REMOVE")


def _names(clause: str) -> list[str]:
    """Split a names clause on commas/``and``/``&`` into identifier tokens (order-preserving)."""
    out: list[str] = []
    for tok in re.split(r"\s*,\s*|\s+and\s+|\s*&\s*", clause.strip()):
        tok = tok.strip().strip(".")
        if tok and tok not in out:
            out.append(tok)
    return out


def _slug(text: str, words: int = 5) -> str:
    """A short human title — the first few words, no trailing period."""
    return " ".join(text.split()[:words]).rstrip(".")


# Connector keywords are matched case-insensitively as whole words; the leading verb is exact.
def _split_because(rest: str) -> tuple[str, str | None]:
    """Peel a trailing ``BECAUSE <reason>`` off a clause (the reason is free text)."""
    m = re.search(r"\bBECAUSE\b\s*(.+)$", rest, re.IGNORECASE | re.DOTALL)
    if not m:
        return rest.strip(), None
    return rest[: m.start()].strip(), m.group(1).strip() or None


def parse(text: str) -> ParsedIntent | None:
    """Parse a canonical statement, or ``None`` if ``text`` is not canonical (freeform).

    The leading token must be an uppercase verb in :data:`VERBS`; otherwise the text is treated as
    freeform prose and left for the LLM ``normalize`` path. Deterministic and offline.
    """
    text = text.strip()
    parts = text.split(None, 1)
    if len(parts) < 2 or parts[0] not in VERBS:
        return None
    verb, rest = parts[0], parts[1]

    if verb == "ADD":
        body, reason = _split_because(rest)
        m = re.search(r"\bUSING\b\s*(.+)$", body, re.IGNORECASE | re.DOTALL)
        needs = _names(m.group(1)) if m else []
        names = _names(body[: m.start()] if m else body)
        if not names:
            return None
        intent = f"Add {', '.join(names)}" + (f" using {', '.join(needs)}" if needs else "")
        return ParsedIntent(verb, intent, slug=_slug(", ".join(names)), provides=names,
                            needs=needs, context=reason, canonical=_canon_add(names, needs, reason))

    if verb == "REPLACE":
        body, reason = _split_because(rest)
        m = re.search(r"^(.+?)\bWITH\b\s*(.+)$", body, re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        name, approach = m.group(1).strip().rstrip("."), m.group(2).strip()
        if not name or not approach:
            return None
        intent = f"Replace {name} with {approach}"
        alt = (name, reason) if reason else None
        return ParsedIntent(verb, intent, slug=_slug(f"Replace {name}"), target=name,
                            alternative=alt, canonical=_canon_replace(name, approach, reason))

    if verb == "EXTEND":
        body, reason = _split_because(rest)
        m = re.search(r"^(.+?)\s+(?:TO|WITH)\s+(.+)$", body, re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        lane, behavior = m.group(1).strip(), m.group(2).strip()
        if not lane or not behavior:
            return None
        intent = f"Extend {lane} to {behavior}"
        return ParsedIntent(verb, intent, slug=_slug(behavior), target=lane,
                            context=reason, canonical=_canon_extend(lane, behavior, reason))

    # REMOVE
    body, reason = _split_because(rest)
    m = re.search(r"^(.+?)\bFROM\b\s*(.+)$", body, re.IGNORECASE | re.DOTALL)
    lane = m.group(2).strip() if m else None
    names = _names(m.group(1) if m else body)
    if not names:
        return None
    intent = f"Remove {', '.join(names)}" + (f" from {lane}" if lane else "")
    return ParsedIntent("REMOVE", intent, slug=_slug(f"Remove {', '.join(names)}"),
                        target=lane, context=reason, canonical=_canon_remove(names, lane, reason))


# -- canonical rendering (for the learnable echo + confirm) -----------------
def _because(reason: str | None) -> str:
    return f" BECAUSE {reason}" if reason else ""


def _canon_add(names: list[str], needs: list[str], reason: str | None) -> str:
    return f"ADD {', '.join(names)}" + (f" USING {', '.join(needs)}" if needs else "") + _because(reason)


def _canon_extend(lane: str, behavior: str, reason: str | None) -> str:
    return f"EXTEND {lane} TO {behavior}" + _because(reason)


def _canon_replace(name: str, approach: str, reason: str | None) -> str:
    return f"REPLACE {name} WITH {approach}" + _because(reason)


def _canon_remove(names: list[str], lane: str | None, reason: str | None) -> str:
    return f"REMOVE {', '.join(names)}" + (f" FROM {lane}" if lane else "") + _because(reason)


def render(*, provides: list[str], needs: list[str], intent: str, lane: str | None = None) -> str:
    """Render a planned node back to a canonical statement (the legacy-path echo).

    A node folded into an existing ``lane`` reads as ``EXTEND``; a fresh capability reads as ``ADD``
    of the names it provides (falling back to its intent text when it declared none).
    """
    if lane:
        return _canon_extend(lane, _slug(intent, 8), None)
    if provides:
        return _canon_add(provides, needs, None)
    return f"ADD {_slug(intent, 8)}"


# -- freeform -> canonical (the one LLM touch) ------------------------------
_NORMALIZE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "statements": {
            "type": "array",
            "items": {"type": "string"},
            "description": "canonical DSL statements, one per capability",
        }
    },
    "required": ["statements"],
}

_NORMALIZE_SYSTEM = """You translate a freeform coding intent into semi-git's canonical intent DSL.
Emit ONE statement per coherent capability a reviewer would version as a unit — usually 1-3, rarely
more. Use ONLY these forms, each on its own line, verbs UPPERCASE:

  ADD <names> [USING <names>] [BECAUSE <reason>]      -- a new capability; <names> are the top-level
                                                         names it defines; USING lists names it needs
  EXTEND <lane> TO <behavior> [BECAUSE <reason>]       -- enhance something that already exists
  REPLACE <name> WITH <approach> [BECAUSE <reason>]    -- swap an existing approach; reason is why the
                                                         old one lost
  REMOVE <names> [FROM <lane>] [BECAUSE <reason>]      -- delete existing def(s)

Rules: <names> are identifiers (snake_case/CamelCase), comma-separated. Prefer ADD for new work and
EXTEND/REPLACE only when the codebase already has the thing. Fold helpers into their capability's
<names> rather than emitting a statement per function. Put a genuine rationale in BECAUSE when the
intent implies one; omit it otherwise. Do not invent capabilities beyond the intent."""


def normalize(text: str, codebase=None, *, client, model: str) -> list[str]:
    """LLM: render freeform ``text`` into a list of canonical statements (one per capability).

    Returns ``[]`` on any failure so the caller degrades to the legacy planner. ``codebase`` (a
    ``Codebase`` dict) is summarized for grounding when provided.
    """
    files = ", ".join(sorted(codebase)) if codebase else "(empty / new project)"
    user = f"Existing files: {files}\n\nFreeform intent:\n{text}"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": _NORMALIZE_SYSTEM},
                      {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "dsl", "schema": _NORMALIZE_SCHEMA, "strict": True}},
            temperature=0,
        )
        payload = json.loads(resp.choices[0].message.content)
    except Exception:  # noqa: BLE001 — any failure degrades to the legacy planner
        return []
    return [s.strip() for s in payload.get("statements", []) if s and s.strip()]
