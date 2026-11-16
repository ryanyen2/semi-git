"""Configuration: load `.env` and build the OpenAI client; load the oracle's tier config.

ugh's graph-reasoning agents (the planner and the distillation labeler) speak the OpenAI
API — they reason about the graph, never author code. The endpoint is env-driven: a bare
`OpenAI()` honors `OPENAI_BASE_URL`/`OPENAI_API_KEY`, so pointing those at any OpenAI-compatible
proxy (e.g. a litellm gateway serving Claude models) routes every agent there with no code
change — pick the model with `UGH_MODEL`. The key/base live in `.env` at the repo root; we parse
it without adding a dotenv dependency.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path

from ugh import state

# Every graph-reasoning path shares this tier, and the labeler alone fires once
# per leaf and subsystem on the `ugh log --refresh` hot path, so the default is
# chosen for cost. `gpt-5.6-luna` takes the same `responses.parse` +
# `text_format` + `reasoning={"effort": ...}` shape every call site already
# sends; its accepted efforts are none/low/medium/high/xhigh/max, so the pinned
# `low` is valid (it rejects `minimal`, which nothing here sends).
DEFAULT_MODEL = "gpt-5.6-luna"

# The OpenAI SDK's `responses.parse` re-serializes the endpoint's raw response (pydantic
# `model_dump`) to hand structured output back. When UGH_MODEL is a Claude model served through a
# litellm proxy, that response carries a `ResponseReasoningItem` shaped the way the proxy emits it
# (id ending `...assistant`, phase=None) rather than OpenAI's native shape, so pydantic's union
# serializer for the `output` field prints a benign `UserWarning` on every call -- pure noise that
# floods the terminal when `ugh map`/`ugh graph --refresh` fires a batch of labeling calls. The
# parsed result is correct regardless; suppress just this one message so the graph render stays
# readable. Scoped by message+category+module so nothing else is hidden.
warnings.filterwarnings(
    "ignore",
    message="Pydantic serializer warnings",
    category=UserWarning,
    module="pydantic.main",
)


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


def get_model(repo_path: str | Path = ".") -> str:
    """The model every graph-reasoning agent uses (labeler, planner, intent resolver/theme, repair
    proposer -- they all share one tier). Env-driven so a repo can switch provider/model without a
    code change: `UGH_MODEL` (ugh-specific) wins, else `OPENAI_MODEL`, else `DEFAULT_MODEL`. Loads
    `.env` first so a value set only there is honored regardless of call order."""
    load_env(repo_path)
    return os.environ.get("UGH_MODEL") or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL


def resolve_api_key(repo_path: str | Path = ".", shell_openai_key: str | None = None) -> str | None:
    """The bearer token for the OpenAI-compatible endpoint, resolved so the *live* credential wins
    over a stale checked-in one:

      1. an `OPENAI_API_KEY` the user **explicitly exported in the shell** always wins -- never
         override a deliberate setup;
      2. else, for a **Claude model** (the repo's `UGH_MODEL=claude-*` via-proxy pattern), the live
         Claude token `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_API_KEY` (what Claude Code already holds) --
         so a rotated/stale `OPENAI_API_KEY` line in `.env` no longer silently 401s every agent;
      3. else `.env`'s `OPENAI_API_KEY`; else any Anthropic token.

    Same bearer for both providers: the litellm gateway accepts whichever token is valid *on it*.
    Pass `shell_openai_key` = `os.environ.get("OPENAI_API_KEY")` captured *before* `load_env`, so
    the ".env vs shell" distinction survives `load_env`'s `setdefault`."""
    if shell_openai_key:
        return shell_openai_key
    load_env(repo_path)
    anthropic = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if get_model(repo_path).lower().startswith("claude") and anthropic:
        return anthropic
    return os.environ.get("OPENAI_API_KEY") or anthropic


def get_client(repo_path: str | Path = "."):
    """Build an OpenAI client. The key is resolved by `resolve_api_key` (live Claude token beats a
    stale `.env` `OPENAI_API_KEY` for a Claude model); the base URL still comes from
    `OPENAI_BASE_URL`."""
    shell_openai_key = os.environ.get("OPENAI_API_KEY")  # capture BEFORE load_env writes the .env one
    load_env(repo_path)
    key = resolve_api_key(repo_path, shell_openai_key)
    if not key:
        raise RuntimeError(
            "No LLM credential found: set OPENAI_API_KEY (or ANTHROPIC_AUTH_TOKEN for a Claude "
            "model) in the environment or .env")
    from openai import OpenAI

    # A bounded timeout so a slow/stalled endpoint raises (→ the labeler's deterministic offline
    # fallback) instead of hanging `ugh map` indefinitely -- the SDK default is 600s, which reads
    # to a user as "spinning forever". 60s is well above normal label latency.
    return OpenAI(api_key=key, timeout=60.0)


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

