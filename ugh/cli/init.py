"""`init` binds the repo and mines its history (from `--horizon` on, if given) and installs the
capture hooks; `mcp` runs the stdio MCP server."""

from __future__ import annotations


def register(subs, parent) -> None:
    ip = subs.add_parser("init", parents=[parent])
    ip.add_argument("--horizon")
    ip.add_argument("--agent", action="store_true",
                    help="also wire up a coding agent and the VS Code extension: .mcp.json, the "
                         "Claude Code skills, and ugh.path")
    ip.add_argument("path", nargs="?", default=".")
    ip.set_defaults(func=_cmd_init)

    mp = subs.add_parser("mcp", parents=[parent])
    mp.add_argument("path", nargs="?", default=".")
    mp.set_defaults(func=_cmd_mcp)


def _cmd_init(args) -> int:
    from ugh.cli._common import _emit_json, _fail
    from ugh.core.lens import init as kernel_init
    from ugh.store.gitbind import GitError

    try:
        kernel_init(args.path, horizon=args.horizon)
    except (ValueError, GitError) as ex:
        return _emit_json({"error": str(ex)}) if args.as_json else _fail(str(ex))
    from ugh.store.gitbind import GitBinding

    hooked = _install_prompt_hook(args.path)
    activity_hooked = _install_activity_hook(args.path)
    if hooked or activity_hooked:
        GitBinding(args.path).exclude_locally(".claude/settings.local.json")
    # opt-in: a CLI-only user has no use for agent config in their repo
    agent = _install_agent(args.path) if args.agent else None
    # warn now: every commit ugh makes would otherwise be authored "ugh <ugh@ugh.local>"
    placeholder = GitBinding(args.path).placeholder_identity()
    if args.as_json:
        return _emit_json({"ok": True, "path": args.path, "horizon": args.horizon,
                           "hook": hooked, "activity_hook": activity_hooked,
                           "agent": agent, "placeholder_identity": placeholder})
    print(f"✓ initialized ugh kernel in {args.path} (.sgt/ + git)")
    if placeholder:
        print("⚠ this repo has no git identity, so commits will be authored "
              "'ugh <ugh@ugh.local>'. Set yours with:\n"
              "    git config user.name  \"Your Name\"\n"
              "    git config user.email \"you@example.com\"")
    if hooked:
        print("✓ installed Claude Code prompt hook (.claude/settings.local.json) -- your prompts "
              "become local intent evidence; remove the UserPromptSubmit entry to opt out")
    if activity_hooked:
        print("✓ installed Claude Code edit hook -- each Edit/Write becomes a live activity "
              "event; remove the PostToolUse entry to opt out")
    if agent:
        print(f"✓ wired up your coding agent (.mcp.json, {agent['skills']} skills in "
              ".claude/skills/) and the VS Code extension (.vscode/settings.json)\n"
              f"    ugh path: {agent['ugh_path']}")
    elif args.agent:
        print("⚠ could not write the agent config (.mcp.json, .claude/, .vscode/). The repo is "
              "initialized; re-run `ugh init --agent` once the directory is writable.")
    else:
        print("→ using Claude Code or the VS Code extension? run `ugh init --agent` to wire them up")
    return 0


def _install_prompt_hook(path: str) -> bool:
    """Merge a `UserPromptSubmit` hook running `ugh intent record` into the repo's per-user
    `.claude/settings.local.json`, so each prompt typed to Claude Code becomes a local turn. The
    command uses the running `ugh`'s absolute path, since the hook's shell may not have the venv on
    its PATH. Idempotent; a failure means no hook, never a failed init. Returns whether it is
    installed."""
    import json
    from pathlib import Path

    from ugh.agent_assets.install import resolve_ugh_path

    try:
        p = Path(path) / ".claude" / "settings.local.json"
        settings = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        hooks = settings.setdefault("hooks", {})
        entries = hooks.setdefault("UserPromptSubmit", [])
        if any("intent record" in (h.get("command") or "")
               for e in entries for h in e.get("hooks", [])):
            return True  # already installed (any variant of the command counts)
        cmd = f'"{resolve_ugh_path()}" intent record'
        entries.append({"hooks": [{"type": "command", "command": cmd}]})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 -- capture setup must never fail an init
        return False


def _install_activity_hook(path: str) -> bool:
    """Like `_install_prompt_hook`, for a `PostToolUse` hook running `ugh intent activity` on every
    Edit, Write and MultiEdit. Returns whether it is installed."""
    import json
    from pathlib import Path

    from ugh.agent_assets.install import resolve_ugh_path

    try:
        p = Path(path) / ".claude" / "settings.local.json"
        settings = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        hooks = settings.setdefault("hooks", {})
        entries = hooks.setdefault("PostToolUse", [])
        if any("intent activity" in (h.get("command") or "")
               for e in entries for h in e.get("hooks", [])):
            return True  # already installed
        cmd = f'"{resolve_ugh_path()}" intent activity'
        entries.append({"matcher": "Edit|Write|MultiEdit",
                        "hooks": [{"type": "command", "command": cmd}]})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 -- capture setup must never fail an init
        return False


def _install_agent(path: str) -> dict | None:
    """Write the agent and editor config (`ugh init --agent`), or return None when it cannot be
    written; the repo stays initialized either way."""
    from ugh.agent_assets.install import install_agent

    try:
        return install_agent(path)
    except OSError:
        return None


def _cmd_mcp(args) -> int:
    from ugh.mcp import serve

    serve(args.path)
    return 0
