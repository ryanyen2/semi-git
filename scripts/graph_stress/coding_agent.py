"""The coding agent that stands in for a human/agent implementing a planned node.

sgt never authors code; this harness component does — exactly the role sgt expects an external
agent to play. Given the current files and one planned node (intent + the names it must `provide`
and may `need`), it returns the complete new content of each file it writes, then the driver
checkpoints that drift under the node. Grounded to define the declared `provides` identifiers so
we can measure planner-vs-reality name drift (the `preprocess` vs `preprocess_data` failure mode).
"""

from __future__ import annotations

import json

from sgt.config import get_client, get_model

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "the COMPLETE new file content"},
                },
                "required": ["path", "content"],
            },
        },
        "defined": {
            "type": "array", "items": {"type": "string"},
            "description": "the top-level names you actually defined in this change",
        },
    },
    "required": ["files", "defined"],
}

_SYS = (
    "You are a coding agent implementing ONE sub-task inside an existing Python project. You write "
    "code only; you do not manage version control. You are given the current files and a sub-task: "
    "its intent, the top-level names it must `provide`, and names it may `need` from existing code. "
    "Rules: (1) define EXACTLY the identifiers listed in `provides` at top level, spelled identically; "
    "(2) when a `needs` name exists in the current files, import/call it rather than redefining it; "
    "(3) ADD to existing files — return their full content with your additions, never delete prior "
    "code; (4) keep functions small and importable; no network calls, no external services. "
    "Return the complete content of every file you touch, plus the list of names you defined."
)


def implement(repo_path: str, files: dict[str, str], intent: str,
              provides: list[str], needs: list[str], *, client=None, model: str | None = None) -> dict:
    """Implement one planned node. Returns ``{files: {path: content}, defined: [...]}``."""
    client = client or get_client(repo_path)
    cur = "\n\n".join(f"### {p}\n```python\n{src}\n```" for p, src in sorted(files.items())) or "(empty project)"
    user = (
        f"Current files:\n{cur}\n\n"
        f"Sub-task intent: {intent}\n"
        f"provides (define these exactly): {provides or '(none stated)'}\n"
        f"needs (use if present): {needs or '(none)'}\n"
    )
    resp = client.chat.completions.create(
        model=model or get_model(),
        messages=[{"role": "system", "content": _SYS}, {"role": "user", "content": user}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "impl", "schema": _SCHEMA, "strict": True}},
        temperature=0,
    )
    payload = json.loads(resp.choices[0].message.content)
    return {
        "files": {f["path"]: f["content"] for f in payload.get("files", [])},
        "defined": payload.get("defined", []),
    }
