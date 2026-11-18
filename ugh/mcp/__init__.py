"""Model Context Protocol surface for ugh.

A dependency-free MCP stdio server that gives any MCP client (Claude Code, other agents) the same
verbs the CLI has: an agent edits files however it likes, saves its work with a name, and reads,
takes out and brings back steps by address.
"""

from ugh.mcp.server import TOOLS, call_tool, handle_request, serve

__all__ = ["TOOLS", "call_tool", "handle_request", "serve"]
