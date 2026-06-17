"""semi-git's owned agents: prompt classification, fuzzy ref resolution.

These policies are LLM/deterministic baselines architected to be RL-trainable later
(plan KTD7). Resolution is deterministic-first; classification calls the model.
"""

from sgt.agents.resolve import ResolveResult, resolve_ref

__all__ = ["ResolveResult", "resolve_ref"]
