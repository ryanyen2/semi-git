"""Bootstrap verbs: `init` binds git + the kernel op store (mining existing history, or -- with
`--horizon` -- only from a given commit on, R10), and `mcp` runs the stdio MCP server for
coding-agent clients."""

from __future__ import annotations


def register(subs, parent) -> None:
    ip = subs.add_parser("init", parents=[parent])
    ip.add_argument("--horizon")
    ip.add_argument("path", nargs="?", default=".")
    ip.set_defaults(func=_cmd_init)

    mp = subs.add_parser("mcp", parents=[parent])
    mp.add_argument("path", nargs="?", default=".")
    mp.set_defaults(func=_cmd_mcp)


def _cmd_init(args) -> int:
    from sgt.core.lens import init as kernel_init

    kernel_init(args.path, horizon=args.horizon)
    print(f"✓ initialized sgt kernel in {args.path} (.sgt/ + git)")
    return 0


def _cmd_mcp(args) -> int:
    from sgt.mcp import serve

    serve(args.path)  # stdio MCP server for coding-agent clients
    return 0
