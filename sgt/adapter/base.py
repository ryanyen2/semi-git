"""The coding-agent adapter contract and its normalized result schema.

A backend receives a scoped task (intent + current codebase + allowed files) and
returns an ``AgentResult`` carrying typed effects. The flat per-effect schema below
is what the OpenAI backend asks the model to fill via structured output, and is
mapped to `Effect` objects here so backends never construct `Effect` directly.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from sgt.effects.model import Codebase, Effect, EffectOp


class AgentStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    SCOPE_VIOLATION = "scope_violation"


@dataclass
class AgentResult:
    status: AgentStatus
    summary: str = ""
    effects: list[Effect] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    out_of_scope: list[Effect] = field(default_factory=list)
    error: str = ""


# The strict JSON schema for one effect (flat so structured output stays valid).
EFFECT_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "file": {"type": "string", "description": "repo-relative .py path"},
        "op": {"type": "string", "enum": [o.value for o in EffectOp]},
        "target": {
            "type": "string",
            "description": "primary name: def/class name, constant name, in_func, or old name",
        },
        "source": {
            "type": "string",
            "description": "full source for add_def (a def/class) or add_import (an import line); else empty",
        },
        "value": {"type": "string", "description": "literal value for set_const; else empty"},
        "new_name": {"type": "string", "description": "new name for rename_def; else empty"},
        "callee": {"type": "string", "description": "callee name for add_call; else empty"},
    },
    "required": ["file", "op", "target", "source", "value", "new_name", "callee"],
}

RESULT_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "effects": {"type": "array", "items": EFFECT_JSON_SCHEMA},
        "depends_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": "names of existing features/concepts this work depends on",
        },
    },
    "required": ["summary", "effects", "depends_on"],
}


def _coerce_value(raw: str):
    """Parse a set_const literal: try a Python literal, fall back to the string."""
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw


def _effect_from_flat(d: dict) -> Effect:
    op = EffectOp(d["op"])
    file = d["file"]
    target = d["target"]
    if op is EffectOp.ADD_DEF:
        return Effect.add_def(file, target, d.get("source", ""))
    if op is EffectOp.ADD_IMPORT:
        return Effect.add_import(file, d.get("source", "") or target)
    if op is EffectOp.SET_CONST:
        return Effect.set_const(file, target, _coerce_value(d.get("value", "")))
    if op is EffectOp.RENAME_DEF:
        return Effect.rename_def(file, target, d.get("new_name", ""))
    if op is EffectOp.ADD_CALL:
        return Effect.add_call(file, target, d.get("callee", ""))
    raise ValueError(f"unknown op {op}")


def effects_from_payload(effect_dicts: list[dict]) -> list[Effect]:
    """Map the model's flat effect dicts to typed `Effect`s."""
    return [_effect_from_flat(d) for d in effect_dicts]


class CodingAgentAdapter(Protocol):
    """Backend-agnostic dispatch contract (JDBC-driver style; see plan KTD4)."""

    name: str

    def execute_task(
        self,
        intent: str,
        codebase: Codebase,
        allowed_files: set[str] | None = None,
    ) -> AgentResult: ...
