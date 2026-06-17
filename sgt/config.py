"""Configuration: load `.env` and build the OpenAI client.

semi-git's owned agents (classifier, coding backend) run on the OpenAI API. The key
lives in `.env` at the repo root; we parse it without adding a dotenv dependency.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MODEL = "gpt-4o"


def load_env(repo_path: str | Path = ".") -> None:
    env_path = Path(repo_path) / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def get_model() -> str:
    return os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)


def get_client(repo_path: str | Path = "."):
    """Build an OpenAI client, loading the key from `.env` if not already in env."""
    load_env(repo_path)
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")
    from openai import OpenAI

    return OpenAI()
