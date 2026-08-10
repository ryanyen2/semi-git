"""`sgt init --agent` writes a repo's agent and editor config.

The failure this guards against is silent: a config that names a `sgt` the agent or the extension
cannot actually run leaves both surfaces dead with no error anyone sees. So the assertions here are
mostly about the paths being absolute, and about merging into whatever config is already there
rather than replacing it.
"""

from __future__ import annotations

import json

from sgt.agent_assets.install import install_agent, resolve_sgt_path


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_writes_all_four_files(tmp_path):
    report = install_agent(tmp_path)

    assert _read(tmp_path / ".mcp.json")["mcpServers"]["sgt"]["args"] == ["mcp"]
    assert "sgt" in _read(tmp_path / ".claude" / "settings.json")["enabledMcpjsonServers"]
    assert _read(tmp_path / ".vscode" / "settings.json")["sgt.path"] == report["sgt_path"]
    assert (tmp_path / ".claude" / "skills" / "sgt-agent" / "SKILL.md").is_file()
    assert report["skills"] == 3


def test_paths_are_absolute(tmp_path):
    """A relative command works only when the agent happens to run from the right directory, and
    a bare `sgt` works only when it happens to be on that process's PATH. Neither is guaranteed."""
    install_agent(tmp_path)

    command = _read(tmp_path / ".mcp.json")["mcpServers"]["sgt"]["command"]
    # `resolve_sgt_path` degrades to the bare name when nothing resolves, e.g. under a test runner
    # with no `sgt` installed. Absolute is the contract whenever it resolved to a real file.
    if command != "sgt":
        assert command.startswith("/")
        assert command == _read(tmp_path / ".vscode" / "settings.json")["sgt.path"]


def test_merges_into_existing_config(tmp_path):
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "settings.json").write_text(
        json.dumps({"editor.tabSize": 2}), encoding="utf-8"
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "other-server"}}}), encoding="utf-8"
    )

    install_agent(tmp_path)

    vscode_settings = _read(tmp_path / ".vscode" / "settings.json")
    assert vscode_settings["editor.tabSize"] == 2
    assert "sgt.path" in vscode_settings
    servers = _read(tmp_path / ".mcp.json")["mcpServers"]
    assert servers["other"]["command"] == "other-server"
    assert "sgt" in servers


def test_is_idempotent(tmp_path):
    install_agent(tmp_path)
    first = (tmp_path / ".mcp.json").read_text(), (tmp_path / ".claude" / "settings.json").read_text()

    install_agent(tmp_path)

    assert first == (
        (tmp_path / ".mcp.json").read_text(),
        (tmp_path / ".claude" / "settings.json").read_text(),
    )
    # Re-running must not append a second "sgt" to the approval list.
    assert _read(tmp_path / ".claude" / "settings.json")["enabledMcpjsonServers"] == ["sgt"]


def test_unreadable_config_does_not_abort(tmp_path):
    """A hand-broken JSON file should not stop setup. Being asked to re-run after fixing your JSON
    is worse than having the file rewritten, because the surfaces stay dead until you do."""
    (tmp_path / ".mcp.json").write_text("{ not json", encoding="utf-8")

    install_agent(tmp_path)

    assert "sgt" in _read(tmp_path / ".mcp.json")["mcpServers"]


def test_resolve_sgt_path_returns_a_string(tmp_path):
    assert isinstance(resolve_sgt_path(), str)
