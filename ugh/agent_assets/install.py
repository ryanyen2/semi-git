"""Wire a repo up for a coding agent and the VS Code extension (`ugh init --agent`).

Four small files, each merged rather than overwritten so an existing config survives:

* `.mcp.json` -- the project-scoped MCP server Claude Code offers on first run in the repo.
* `.claude/settings.json` -- pre-approves that server, so nobody has to answer the prompt.
* `.claude/skills/` -- the `ugh-agent` / `ugh-plan` / `ugh-workflow` skills, copied out of the
  installed package.
* `.vscode/settings.json` -- `ugh.path`, so the extension does not have to find `ugh` on PATH.

Every command is written with the *absolute* path of the running `ugh`, for the reason
`_install_prompt_hook` already documents: hooks, MCP servers, and the extension host all run in
shell environments that need not have the install on their PATH. A GUI-launched VS Code inherits
the login shell's PATH, not the terminal's, so a bare `ugh` is a coin flip. An absolute path is not.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from ugh.agent_assets import SKILLS_DIR


def resolve_ugh_path() -> str:
    """Absolute path of the running `ugh`, or the bare name if it cannot be resolved.

    `sys.argv[0]` is the console script when invoked as `ugh`; under `python -m ugh.cli` or pytest
    it is something else, so fall back to a PATH lookup and finally to the bare name (which at
    least works wherever PATH happens to be right).
    """
    exe = Path(sys.argv[0])
    if exe.name != "ugh":
        found = shutil.which("ugh")
        if not found:
            return "ugh"
        exe = Path(found)
    return str(exe.resolve())


def _read_json(path: Path) -> dict:
    """Existing config, or `{}` if absent/unparseable. A hand-broken file must not abort setup."""
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def install_mcp_json(repo: Path, ugh_path: str) -> bool:
    """Register the `ugh` stdio server in `.mcp.json`, preserving any other servers."""
    path = repo / ".mcp.json"
    data = _read_json(path)
    servers = data.setdefault("mcpServers", {})
    servers["ugh"] = {"command": ugh_path, "args": ["mcp"]}
    _write_json(path, data)
    return True


def install_mcp_approval(repo: Path) -> bool:
    """Pre-approve the project server in the *shared* `.claude/settings.json`.

    Claude Code otherwise prompts once per repo before enabling a project-scoped server. This goes
    in the tracked settings file, not `settings.local.json`, so it survives a clone -- the local
    file is gitignored by convention and would leave every fresh checkout facing the prompt again.
    """
    path = repo / ".claude" / "settings.json"
    data = _read_json(path)
    enabled = data.setdefault("enabledMcpjsonServers", [])
    if "ugh" not in enabled:
        enabled.append("ugh")
    _write_json(path, data)
    return True


def install_skills(repo: Path) -> int:
    """Copy the bundled Claude Code skills into `.claude/skills/`. Returns how many were written.

    Overwrites on re-run so `ugh init --agent` after an upgrade refreshes stale skill text; skills
    are generated content, and a repo that edited them in place has no way to say so anyway.
    """
    if not SKILLS_DIR.is_dir():
        return 0
    dest_root = repo / ".claude" / "skills"
    written = 0
    for src in sorted(SKILLS_DIR.iterdir()):
        if not src.is_dir():
            continue
        dest = dest_root / src.name
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
        written += 1
    return written


def install_vscode_settings(repo: Path, ugh_path: str) -> bool:
    """Point the VS Code extension straight at this `ugh`, bypassing PATH resolution entirely."""
    path = repo / ".vscode" / "settings.json"
    data = _read_json(path)
    data["ugh.path"] = ugh_path
    _write_json(path, data)
    return True


def install_agent(repo_path: str | Path) -> dict:
    """Run the whole agent wiring. Returns a report of what was written."""
    repo = Path(repo_path)
    ugh_path = resolve_ugh_path()
    return {
        "ugh_path": ugh_path,
        "mcp_json": install_mcp_json(repo, ugh_path),
        "mcp_approval": install_mcp_approval(repo),
        "skills": install_skills(repo),
        "vscode_settings": install_vscode_settings(repo, ugh_path),
    }
