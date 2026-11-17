"""The oracle: async tiered build/test verdicts attached to ideals (ADR S6; plan U9, R13).

Ordinary materialization (`lens.put`) never touches this module: a verdict
doesn't exist ("pending") until something explicitly runs it. A verdict is keyed to the exact ideal
it was run against (a hash of its sorted op-id set, `ideal_key`), never to a ref, so an edit that
changes the ideal produces a new key and the old verdict silently stops applying.

`.sgt/oracle.json` (committed, team-shared -- see `ugh.config.load_oracle_config`) declares tier
commands in run order. `.sgt/local/oracle.json` (gitignored) is the per-ideal verdict cache.

`run`/`verdict_for`/`override` all take an explicit `ideal` (defaulting to the current ref's
committed one), so a *candidate* ideal that isn't committed yet can be gated on its verdict.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from ugh import state
from ugh.config import load_oracle_config
from ugh.core import lens
from ugh.core.ideal import Ideal

_OUTPUT_TAIL_CHARS = 4000


def ideal_key(ideal: Ideal) -> str:
    """A stable key for the exact op-id set materialized -- two ideals with the same ops (even
    across different refs) share a verdict; any edit changes the key."""
    return sha256(",".join(sorted(ideal.op_ids)).encode("utf-8")).hexdigest()[:16]


def _load_verdicts(repo: Path) -> dict:
    return state.load_json(repo, "verdicts", default={})


def _save_verdicts(repo: Path, table: dict) -> None:
    state.save_json(repo, "verdicts", table)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_tier(repo: Path, command: str) -> dict:
    try:
        proc = subprocess.run(command, shell=True, cwd=repo, capture_output=True, text=True)
        output = proc.stdout + proc.stderr
        return {
            "status": "pass" if proc.returncode == 0 else "fail",
            "exit_code": proc.returncode,
            "output_tail": output[-_OUTPUT_TAIL_CHARS:],
        }
    except OSError as e:
        return {"status": "fail", "exit_code": -1, "output_tail": str(e)}


def check(repo: str | Path) -> tuple[bool, str] | None:
    """Run the configured tiers on the working tree as it is, stopping at the first failure:
    `(passed, output)`, or None when no check is configured. Records nothing."""
    cfg = load_oracle_config(repo)
    if cfg is None or not cfg.tiers:
        return None
    output = []
    for t in cfg.tiers:
        try:
            proc = subprocess.run(t.command, shell=True, cwd=repo, capture_output=True, text=True)
        except OSError as e:
            return False, str(e)
        output.append(proc.stdout + proc.stderr)
        if proc.returncode != 0:
            return False, "".join(output)
    return True, "".join(output)


def verdict_for(repo: str | Path, ideal: Ideal) -> dict | None:
    """Pure read: the stored verdict record (`{"tiers": ..., "override": ...}`) for exactly this
    ideal, or `None` if nothing has been recorded yet (pending)."""
    return _load_verdicts(Path(repo)).get(ideal_key(ideal))


def overall_status(record: dict | None) -> str:
    """override wins if present; else "fail" if any recorded tier failed; else "pass" if at
    least one tier ran; else "pending" (nothing recorded yet, or config exists but nothing has
    been run against this exact ideal)."""
    if record is None:
        return "pending"
    override_rec = record.get("override")
    if override_rec:
        return override_rec["status"]
    tiers = record.get("tiers", {})
    if any(t["status"] == "fail" for t in tiers.values()):
        return "fail"
    return "pass" if tiers else "pending"


def run(repo: str | Path, ideal: Ideal | None = None, tier: str | None = None) -> dict:
    """Run configured tiers (in declared order, stopping at the first failure) against `ideal`
    (default: the current ref's committed ideal), recording each tier's `{status, exit_code,
    output_tail}`. `tier` runs just that one tier regardless of pipeline position -- a direct
    re-run that replaces any stale result for that tier. No config -> returns
    `{"configured": False, "tiers": {}, "override": None}` and writes nothing; the caller (CLI)
    is responsible for the loud warning (R13) -- this module stays side-effect-documented and
    presentation-free, matching the rest of `ugh/core/`."""
    repo = Path(repo)
    if ideal is None:
        ideal = lens.current_ideal(repo)
    cfg = load_oracle_config(repo)
    if cfg is None:
        return {"configured": False, "tiers": {}, "override": None}

    key = ideal_key(ideal)
    table = _load_verdicts(repo)
    record = table.get(key, {"tiers": {}, "override": None})
    tiers = dict(record.get("tiers", {}))

    names = [t.name for t in cfg.tiers]
    if tier is not None and tier not in names:
        raise ValueError(f"unknown tier {tier!r}; configured tiers: {names}")

    for t in cfg.tiers:
        if tier is not None and t.name != tier:
            continue
        result = _run_tier(repo, t.command)
        tiers[t.name] = result
        if tier is None and result["status"] != "pass":
            break  # stop the pipeline at the first failure, like a real CI run

    record = {"tiers": tiers, "override": record.get("override")}
    table[key] = record
    _save_verdicts(repo, table)
    return {"configured": True, **record}


def override(
    repo: str | Path, status: str, reason: str, by: str | None = None, ideal: Ideal | None = None,
) -> dict:
    """Record a human verdict that supersedes tier results for `overall_status` -- the escape
    hatch for a flaky tier or a deliberate, attributed exception. Independent of whether an
    oracle is configured at all (a repo with zero automated tiers can still be human-gated)."""
    if status not in ("pass", "fail"):
        raise ValueError(f"status must be 'pass' or 'fail', got {status!r}")
    repo = Path(repo)
    if ideal is None:
        ideal = lens.current_ideal(repo)
    key = ideal_key(ideal)
    table = _load_verdicts(repo)
    record = table.get(key, {"tiers": {}})
    record["override"] = {"status": status, "reason": reason, "by": by, "ts": _now()}
    table[key] = record
    _save_verdicts(repo, table)
    return record
