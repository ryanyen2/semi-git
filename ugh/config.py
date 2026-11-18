"""Configuration: the oracle's tier config, the project's own check (`.sgt/oracle.json`)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ugh import state


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
    body = state.load_json(repo_path, "oracle_config")
    if body is None:
        return None
    tiers = tuple(OracleTier(name=t["name"], command=t["command"]) for t in body.get("tiers", []))
    return OracleConfig(tiers=tiers)

