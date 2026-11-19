"""Check verdicts stored per ideal in `.sgt/local/oracle.json`, keyed to the exact op-id set they
were recorded against (`ideal_key`), so a changed ideal has no verdict until one is recorded for it.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from ugh import state
from ugh.core.ideal import Ideal


def ideal_key(ideal: Ideal) -> str:
    """A stable key for the exact op-id set materialized -- two ideals with the same ops (even
    across different refs) share a verdict; any edit changes the key."""
    return sha256(",".join(sorted(ideal.op_ids)).encode("utf-8")).hexdigest()[:16]


def _load_verdicts(repo: Path) -> dict:
    return state.load_json(repo, "verdicts", default={})


def verdict_for(repo: str | Path, ideal: Ideal) -> dict | None:
    """The stored verdict record (`{"tiers", "override"}`) for exactly this ideal, or None."""
    return _load_verdicts(Path(repo)).get(ideal_key(ideal))


def overall_status(record: dict | None) -> str:
    """A verdict record's status, "pass", "fail" or "pending": an override wins, then a failed
    tier fails, then a tier that ran passes; no record is pending."""
    if record is None:
        return "pending"
    override_rec = record.get("override")
    if override_rec:
        return override_rec["status"]
    tiers = record.get("tiers", {})
    if any(t["status"] == "fail" for t in tiers.values()):
        return "fail"
    return "pass" if tiers else "pending"
