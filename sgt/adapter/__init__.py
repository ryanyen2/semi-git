"""Coding-agent delegation: a backend-agnostic contract + the OpenAI backend.

semi-git does not write code itself. It dispatches a scoped task to a coding agent
and receives typed effects back. The OpenAI backend uses structured output so the
agent emits effects directly (the strongest effect-extraction tier — no post-hoc
diff parsing needed).
"""

from sgt.adapter.base import AgentResult, AgentStatus, CodingAgentAdapter, effects_from_payload
from sgt.adapter.openai_agent import OpenAICodingAgent

__all__ = [
    "AgentResult",
    "AgentStatus",
    "CodingAgentAdapter",
    "effects_from_payload",
    "OpenAICodingAgent",
]
