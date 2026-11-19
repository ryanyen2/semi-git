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
    """The committed `.sgt/oracle.json`: the project's check as tier commands run in declared
    order, or `None` when the file is absent. JSON because Python 3.10 has no `tomllib`."""
    body = state.load_json(repo_path, "oracle_config")
    if body is None:
        return None
    tiers = tuple(OracleTier(name=t["name"], command=t["command"]) for t in body.get("tiers", []))
    return OracleConfig(tiers=tiers)

