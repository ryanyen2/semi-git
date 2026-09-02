#!/usr/bin/env python3
"""Does the ◆ theme the stage cards name still name itself?

    scripts/study/check_event_theme.py <repo>

Stages 3 and 4 tell the participant which piece of work to take out by its cross-feature theme
label, and `./stage` finds that label by squashing every theme's name and looking for "event day"
(`scripts/study/task-scripts/stage`, `theme_label`). The label is written by an LLM at rebuild
time, with each save's captured ask as context -- so it is a name that can move, and the way it
fails is a participant handed a stage card naming work the repository does not have.

Prints the label and exits 0 when it matches, exits 1 when nothing does. The same squash the stage
script uses, deliberately: a check that accepted a name the script would reject would be worse than
no check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 2
    path = Path(sys.argv[1]) / ".sgt" / "intent" / "themes.json"
    if not path.is_file():
        print(f"  no themes in {sys.argv[1]} -- run `sgt log --rebuild` first", file=sys.stderr)
        return 1
    squash = lambda t: "".join(c for c in (t or "").casefold() if c.isalnum())
    body = json.loads(path.read_text(encoding="utf-8")).get("data", {})
    for value in body.values():
        if isinstance(value, dict) and "eventday" in squash(value.get("label", "")):
            print(f'  the stage cards will say: {value["label"]}')
            return 0
    names = [v.get("label") for v in body.values() if isinstance(v, dict) and v.get("label")]
    print(f"  no theme names event days. The themes are: {names}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
