"""Bootstrap verbs: `init` binds git + the kernel op store (mining existing history, or -- with
`--horizon` -- only from a given commit on, R10), and `mcp` runs the stdio MCP server for
coding-agent clients."""

from __future__ import annotations


def register(subs, parent) -> None:
    ip = subs.add_parser("init", parents=[parent])
    ip.add_argument("--horizon")
    ip.add_argument("--agent", action="store_true",
                    help="also wire up a coding agent and the VS Code extension: .mcp.json, the "
                         "Claude Code skills, and sgt.path")
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
    activity_hooked = _install_activity_hook(args.path)
    # Opt-in, because plenty of people run `sgt` from the CLI or the TUI alone and have no use for
    # an agent config appearing in their repo. `--agent` is what a coding-agent or VS Code user
    # runs instead.
    agent = _install_agent(args.path) if args.agent else None
    # Never let a repo commit as the placeholder without the user hearing about it: every commit
    # sgt makes here would be authored "semi-git <sgt@semi-git.local>" in git log, blame, and on
    # the remote, and the only moment that is cheap to fix is right now.
    from sgt.store.gitbind import GitBinding
    placeholder = GitBinding(args.path).placeholder_identity()
    if args.as_json:
        return _emit_json({"ok": True, "path": args.path, "horizon": args.horizon,
                           "hook": hooked, "activity_hook": activity_hooked,
                           "agent": agent, "placeholder_identity": placeholder})
    print(f"✓ initialized sgt kernel in {args.path} (.sgt/ + git)")
    if placeholder:
        print("⚠ this repo has no git identity, so commits will be authored "
              "'semi-git <sgt@semi-git.local>'. Set yours with:\n"
              "    git config user.name  \"Your Name\"\n"
              "    git config user.email \"you@example.com\"")
    if hooked:
        print("✓ installed Claude Code prompt hook (.claude/settings.local.json) -- your prompts "
              "become local intent evidence; remove the UserPromptSubmit entry to opt out")
    if activity_hooked:
        print("✓ installed Claude Code edit hook -- each Edit/Write becomes a live activity event "
              "`sgt now` surfaces; remove the PostToolUse entry to opt out")
    if agent:
        print(f"✓ wired up your coding agent (.mcp.json, {agent['skills']} skills in "
              ".claude/skills/) and the VS Code extension (.vscode/settings.json)\n"
              f"    sgt path: {agent['sgt_path']}")
    elif args.agent:
        print("⚠ could not write the agent config (.mcp.json, .claude/, .vscode/). The repo is "
              "initialized; re-run `sgt init --agent` once the directory is writable.")
    else:
        print("→ using Claude Code or the VS Code extension? run `sgt init --agent` to wire them up")
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
    from pathlib import Path

    from sgt.agent_assets.install import resolve_sgt_path

    try:
        p = Path(path) / ".claude" / "settings.local.json"
        settings = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        hooks = settings.setdefault("hooks", {})
        entries = hooks.setdefault("UserPromptSubmit", [])
        if any("intent record" in (h.get("command") or "")
               for e in entries for h in e.get("hooks", [])):
            return True  # already installed (any variant of the command counts)
        cmd = f'"{resolve_sgt_path()}" intent record'
        entries.append({"hooks": [{"type": "command", "command": cmd}]})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 -- capture setup must never fail an init
        return False


def _install_activity_hook(path: str) -> bool:
    """The live-activity companion to `_install_prompt_hook`: merge a `PostToolUse` hook into the
    repo's `.claude/settings.local.json` so every Edit/Write/MultiEdit Claude Code runs is appended
    to the local activity feed via `sgt intent activity` -- the "what the agent is doing right now"
    signal `sgt now` surfaces. `matcher` is a pipe-separated regex over tool names (Claude Code's
    PostToolUse schema), the one structural difference from the prompt hook (which is not tool-
    scoped). Same absolute-path resolution, idempotency (substring check on `intent activity`), and
    best-effort contract; preserves any existing hooks. Returns whether the hook is (now) installed."""
    import json
    from pathlib import Path

    from sgt.agent_assets.install import resolve_sgt_path

    try:
        p = Path(path) / ".claude" / "settings.local.json"
        settings = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        hooks = settings.setdefault("hooks", {})
        entries = hooks.setdefault("PostToolUse", [])
        if any("intent activity" in (h.get("command") or "")
               for e in entries for h in e.get("hooks", [])):
            return True  # already installed
        cmd = f'"{resolve_sgt_path()}" intent activity'
        entries.append({"matcher": "Edit|Write|MultiEdit",
                        "hooks": [{"type": "command", "command": cmd}]})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 -- capture setup must never fail an init
        return False


def _install_agent(path: str) -> dict | None:
    """Write the agent + editor config (`sgt init --agent`). Best-effort, like the hooks: a repo
    that cannot be wired up should still end up initialized, with the failure visible rather than
    fatal."""
    from sgt.agent_assets.install import install_agent

    try:
        return install_agent(path)
    except OSError:
        return None


def _cmd_mcp(args) -> int:
    from sgt.mcp import serve

    serve(args.path)  # stdio MCP server for coding-agent clients
    return 0
