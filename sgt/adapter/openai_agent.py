"""OpenAI coding backend: turn an intent into typed effects via structured output."""

from __future__ import annotations

import json

from sgt.adapter.base import (
    RESULT_JSON_SCHEMA,
    AgentResult,
    AgentStatus,
    effects_from_payload,
)
from sgt.config import get_client, get_model
from sgt.effects.model import Codebase

_SYSTEM = """You are the coding backend for semi-git, a semantic version-control tool.
You do NOT edit files directly. You express a requested change as a list of TYPED
EFFECTS on Python files. The available effect ops are:

- add_def: add a NEW top-level function or class. `target` = its name. `source` =
  the COMPLETE def/class source (valid Python). Use this for almost all new code.
- add_import: add an import. `source` = the import line (e.g. "import hashlib").
  `target` = the same line.
- set_const: change a top-level NAME = constant. `target` = the name, `value` = the
  new literal (e.g. "6").
- rename_def: rename a top-level function. `target` = old name, `new_name` = new name.
- add_call: append a call to `callee` inside the body of function `target`.
- replace_def: REPLACE an existing top-level function's body/signature. `target` =
  the existing function's name. `source` = the COMPLETE rewritten def. Use this to
  CHANGE how an existing function behaves (the function must already exist).

Rules:
- For NEW behavior, prefer add_def and keep each function self-contained.
- To CHANGE an existing function's behavior, use replace_def with the full rewritten
  def — do NOT add_def a second function with the same name (that violates uniqueness).
- Every name a function calls must be defined (in the codebase, added by your
  effects, imported, or a builtin). Add the imports you need.
- Leave unused payload fields as empty strings.
Return effects that, applied in order to the current codebase, implement the intent."""


class OpenAICodingAgent:
    name = "openai"

    def __init__(self, model: str | None = None, repo_path: str = "."):
        self._client = get_client(repo_path)
        self._model = model or get_model()

    def execute_task(
        self,
        intent: str,
        codebase: Codebase,
        allowed_files: set[str] | None = None,
    ) -> AgentResult:
        cb_text = _render_codebase(codebase)
        user = (
            f"Current codebase:\n{cb_text}\n\n"
            f"Intent to implement:\n{intent}\n\n"
            "Emit the typed effects that implement this intent."
        )
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "effect_plan", "schema": RESULT_JSON_SCHEMA, "strict": True},
                },
                temperature=0,
            )
            payload = json.loads(resp.choices[0].message.content)
        except Exception as ex:  # noqa: BLE001 - any backend failure is a failed task
            return AgentResult(status=AgentStatus.FAILED, error=f"{type(ex).__name__}: {ex}")

        try:
            effects = effects_from_payload(payload.get("effects", []))
        except Exception as ex:  # noqa: BLE001
            return AgentResult(status=AgentStatus.FAILED, error=f"malformed effects: {ex}")

        out_of_scope = []
        if allowed_files is not None:
            kept = []
            for e in effects:
                (kept if e.file in allowed_files else out_of_scope).append(e)
            effects = kept

        status = AgentStatus.SCOPE_VIOLATION if out_of_scope else AgentStatus.OK
        return AgentResult(
            status=status,
            summary=payload.get("summary", ""),
            effects=effects,
            depends_on=payload.get("depends_on", []),
            out_of_scope=out_of_scope,
        )


def _render_codebase(cb: Codebase) -> str:
    if not cb:
        return "(empty — this is a new project)"
    parts = []
    for path in sorted(cb):
        body = cb[path] or "(empty)"
        parts.append(f"### {path}\n```python\n{body}\n```")
    return "\n\n".join(parts)
