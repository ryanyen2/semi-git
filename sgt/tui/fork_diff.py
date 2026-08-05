"""Plain (non-Textual) side-by-side fork-tip diff for `sgt resolve` (resolve UX, Phase 2).

`sgt resolve <symbol>` surfaces two divergent tips of one symbol; Phase 1 guarantees the surfaced
fork is *genuinely* divergent, so the two columns always differ (a same-after pseudo-fork never
reaches here). This renders the tips' file content as a two-column diff a user reads before picking a
side -- a pure function over the decoded `fork_detail_view` tips (`{path: content}` per tip), no
Textual dependency, matching the print-based CLI resolve flow (`sgt/cli/resolve.py`).
"""

from __future__ import annotations

import difflib

from sgt.tui.graph import _DIM, _RED, _RESET, _ellipsize, _sgr

_GREEN = "\x1b[32m"
_CONTEXT = 2  # equal lines kept at each hunk boundary before the middle collapses to `… N unchanged …`


def _cell(s: str, width: int, code: str | None, *, color: bool) -> str:
    """One fixed-width column: ellipsize to `width`, left-justify, optionally SGR-wrap (the wrap adds
    no visible width, so justification happens first)."""
    text = _ellipsize(s, width).ljust(width)
    return _sgr(code, text, color=color) if code else text


def _row(left: str, right: str, mark: str, col: int, lcode: str | None, rcode: str | None,
         *, color: bool) -> str:
    return f"{_cell(left, col, lcode, color=color)} {mark} {_cell(right, col, rcode, color=color)}"


def side_by_side(files_a: dict[str, str], files_b: dict[str, str], *, width: int = 120,
                 color: bool = False) -> list[str]:
    """Two-column diff of two fork tips' files. Per path in the tip union, `SequenceMatcher` opcodes
    drive the columns: `equal` runs render dim on both sides (a run longer than `2*_CONTEXT` collapses
    its middle to `… N unchanged …`), `replace` pairs each changed line red-left/green-right with a
    `│` gutter, `delete` shows left-only with a `<` gutter, `insert` right-only with a `>` gutter.
    Long lines ellipsize to the column width. Returns the rendered lines (no trailing newline)."""
    col = max(8, (width - 3) // 2)  # 3-col gutter: " <mark> "
    span = col * 2 + 3
    out: list[str] = []
    for path in sorted(set(files_a) | set(files_b)):
        out.append(_sgr(_DIM, f"── {path} ".ljust(span, "─"), color=color))
        a_lines = files_a.get(path, "").splitlines()
        b_lines = files_b.get(path, "").splitlines()
        sm = difflib.SequenceMatcher(a=a_lines, b=b_lines, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                run = a_lines[i1:i2]
                if len(run) > 2 * _CONTEXT:
                    for ln in run[:_CONTEXT]:
                        out.append(_row(ln, ln, " ", col, _DIM, _DIM, color=color))
                    hidden = len(run) - 2 * _CONTEXT
                    out.append(_sgr(_DIM, f"… {hidden} unchanged …".center(span), color=color))
                    for ln in run[-_CONTEXT:]:
                        out.append(_row(ln, ln, " ", col, _DIM, _DIM, color=color))
                else:
                    for ln in run:
                        out.append(_row(ln, ln, " ", col, _DIM, _DIM, color=color))
            elif tag == "replace":
                left, right = a_lines[i1:i2], b_lines[j1:j2]
                for k in range(max(len(left), len(right))):
                    l = left[k] if k < len(left) else ""
                    r = right[k] if k < len(right) else ""
                    out.append(_row(l, r, "│", col, _RED, _GREEN, color=color))
            elif tag == "delete":
                for ln in a_lines[i1:i2]:
                    out.append(_row(ln, "", "<", col, _RED, None, color=color))
            elif tag == "insert":
                for ln in b_lines[j1:j2]:
                    out.append(_row("", ln, ">", col, None, _GREEN, color=color))
    return out
