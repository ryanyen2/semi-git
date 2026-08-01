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
    from sgt.cli._common import _emit_json, _fail
    from sgt.core.lens import init as kernel_init
    from sgt.store.gitbind import GitError

    try:
        kernel_init(args.path, horizon=args.horizon)
    except (ValueError, GitError) as ex:
        return _emit_json({"error": str(ex)}) if args.as_json else _fail(str(ex))
    hooked = _install_prompt_hook(args.path)
    if args.as_json:
        return _emit_json({"ok": True, "path": args.path, "horizon": args.horizon, "hook": hooked})
    print(f"✓ initialized sgt kernel in {args.path} (.sgt/ + git)")
    if hooked:
        print("✓ installed Claude Code prompt hook (.claude/settings.local.json) -- your prompts "
              "become local intent evidence; remove the UserPromptSubmit entry to opt out")
    return 0


def _install_prompt_hook(path: str) -> bool:
    """Zero-burden capture setup (intent-ledger M1): merge a `UserPromptSubmit` hook into the
    repo's `.claude/settings.local.json` (per-user, gitignored by Claude Code convention -- never
    the shared settings) so every prompt typed to Claude Code is recorded verbatim as a local
    turn via `sgt intent record`. The command is written with the running `sgt`'s absolute path:
    hooks execute in whatever shell environment Claude Code has, and a bare `sgt` silently does
    nothing when the venv isn't on that shell's PATH (testbed 2026-07-31). Idempotent; preserves
    existing settings; any failure means "no hook", never a failed init. Returns whether the hook
    is (now) installed."""
    import json
    import shutil
    import sys
    from pathlib import Path

    try:
        p = Path(path) / ".claude" / "settings.local.json"
        settings = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        hooks = settings.setdefault("hooks", {})
        entries = hooks.setdefault("UserPromptSubmit", [])
        if any("intent record" in (h.get("command") or "")
               for e in entries for h in e.get("hooks", [])):
            return True  # already installed (any variant of the command counts)
        exe = Path(sys.argv[0])
        if exe.name != "sgt":  # e.g. invoked via pytest or `python -m`
            found = shutil.which("sgt")
            exe = Path(found) if found else None
        cmd = f'"{exe.resolve()}" intent record' if exe else "sgt intent record"
        entries.append({"hooks": [{"type": "command", "command": cmd}]})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 -- capture setup must never fail an init
        return False


def _cmd_mcp(args) -> int:
    from sgt.mcp import serve

    serve(args.path)  # stdio MCP server for coding-agent clients
    return 0
