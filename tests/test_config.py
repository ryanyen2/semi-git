"""`sgt.config.resolve_api_key` -- the credential precedence that keeps a stale checked-in
`OPENAI_API_KEY` from silently 401ing every LLM agent when a live Claude token is available. Pure,
no network: `shell_openai_key` is passed explicitly (the value `get_client` captures before
`load_env`), and the model is driven by `SGT_MODEL`."""

from __future__ import annotations

from sgt import config


def test_shell_openai_key_always_wins(monkeypatch, tmp_path):
    # an explicitly shell-exported OPENAI_API_KEY is a deliberate setup -- never override it
    monkeypatch.setenv("SGT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "live-claude")
    assert config.resolve_api_key(tmp_path, shell_openai_key="shell-openai") == "shell-openai"


def test_claude_model_prefers_live_anthropic_over_stale_env_openai(monkeypatch, tmp_path):
    # the bug: .env's OPENAI_API_KEY is stale (rejected 401) but present; for a Claude model the
    # live ANTHROPIC_AUTH_TOKEN (e.g. from Claude Code) must win so labeling actually works
    monkeypatch.setenv("SGT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("OPENAI_API_KEY", "stale-env-openai")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "live-claude")
    assert config.resolve_api_key(tmp_path, shell_openai_key=None) == "live-claude"


def test_claude_model_falls_back_to_openai_when_no_anthropic(monkeypatch, tmp_path):
    monkeypatch.setenv("SGT_MODEL", "claude-haiku-4-5")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai")
    assert config.resolve_api_key(tmp_path, shell_openai_key=None) == "env-openai"


def test_non_claude_model_keeps_openai_even_with_anthropic_present(monkeypatch, tmp_path):
    # a real OpenAI (or non-Claude) model must not be hijacked by an ambient Claude token
    monkeypatch.setenv("SGT_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "claude-tok")
    assert config.resolve_api_key(tmp_path, shell_openai_key=None) == "env-openai"
