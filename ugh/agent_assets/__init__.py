"""Files `ugh init --agent` copies into a repo to wire up a coding agent. `skills/` holds the Claude
Code skills, inside the package so an installed `ugh` can copy them without a checkout.
"""

from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"
