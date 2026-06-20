"""Model Context Protocol surface for semi-git.

A dependency-free MCP stdio server that exposes the semantic tree to any MCP client (Claude
Code, and via the same JSON-RPC surface, other agents). It is the agent-agnostic capability
layer: an external file-editing agent reads the tree, edits files however it likes, then
*checkpoints* its work back into the effect log with a declared intent — the same reconcile
path every agent shares, so merge correctness rides on the log, not the agent.
"""

from sgt.mcp.server import TOOLS, call_tool, handle_request, serve

__all__ = ["TOOLS", "call_tool", "handle_request", "serve"]
