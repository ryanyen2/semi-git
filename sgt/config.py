"""Configuration: load `.env` and build the OpenAI client; load the oracle's tier config.

semi-git's graph-reasoning agents (the planner and the distillation labeler) run on the
OpenAI API — they reason about the graph, never author code. The key lives in `.env` at the
repo root; we parse it without adding a dotenv dependency.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class OracleTier:
    name: str
    command: str


@dataclass(frozen=True)
class OracleConfig:
    tiers: tuple[OracleTier, ...]


def load_oracle_config(repo_path: str | Path = ".") -> OracleConfig | None:
    """Read the team-shared, committed `.sgt/oracle.json` (plan U9, R13): tier commands
    (e.g. parse/build/test), run in declared order. `None` if the file is absent -- the
    "no oracle configured" case, which degrades to a loud warning rather than a fake pass.
    Plain JSON, not TOML: this repo's `requires-python = ">=3.10"` predates stdlib `tomllib`."""
    path = Path(repo_path) / ".sgt" / "oracle.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    tiers = tuple(OracleTier(name=t["name"], command=t["command"]) for t in payload.get("tiers", []))
    return OracleConfig(tiers=tiers)


@dataclass(frozen=True)
class IdentityConstraints:
    """A durable, team-shared correction to the tiered matcher (`sgt.core.identity`), written by
    the `identity split`/`identity join` rewrite verbs (plan U11, R14): pairs of surface ids the
    matcher must never link as a rename/move (``never_link``), or must always link even where the
    hash/fuzzy tiers alone wouldn't find them (``force_link``). Each pair is stored order-
    independently (as a sorted tuple) since a rename's before/after side is a modeling detail,
    not part of the constraint itself."""

    never_link: frozenset[tuple[str, str]] = frozenset()
    force_link: frozenset[tuple[str, str]] = frozenset()


def _identity_constraints_path(repo_path: str | Path = ".") -> Path:
    return Path(repo_path) / ".sgt" / "identity_constraints.json"


def load_identity_constraints(repo_path: str | Path = ".") -> IdentityConstraints:
    """Read the committed `.sgt/identity_constraints.json`. Empty (never `None`) if the file is
    absent, so every caller treats "no constraints" the same as "empty constraints" -- unlike
    `load_oracle_config`, no caller needs an absence check of its own."""
    path = _identity_constraints_path(repo_path)
    if not path.is_file():
        return IdentityConstraints()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return IdentityConstraints(
        never_link=frozenset(tuple(sorted(pair)) for pair in payload.get("never_link", [])),
        force_link=frozenset(tuple(sorted(pair)) for pair in payload.get("force_link", [])),
    )


def save_identity_constraints(repo_path: str | Path, constraints: IdentityConstraints) -> None:
    """Write `.sgt/identity_constraints.json` -- committed, so teammates re-mining the same
    history see the same correction (the escape hatch's whole point)."""
    path = _identity_constraints_path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "never_link": sorted([list(pair) for pair in constraints.never_link]),
        "force_link": sorted([list(pair) for pair in constraints.force_link]),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
