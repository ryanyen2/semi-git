#!/usr/bin/env python3
"""Generate the per-frontier constrain.ts files for the sketchpad history rebuild.

The rebuild exists for one property: each constraint type is one file under
src/kinds/, born whole in the save that introduces the type and never edited again,
plus one side-effect import line in constrain.ts owned by the same save. File and
import line are both units sgt attributes cleanly (the earlier attempt kept the types
as entries in two const tables, and table entries live in residue -- the bytes
between symbols -- where only the newest edit ever reverts clean).

So a frontier's constrain.ts is: the import lines for the kinds that exist yet, plus
the solver machinery in whatever stage it had historically reached (per-point
relaxation, then instances-move-as-one, then the one pass method, then the freedoms
export). The kind files themselves are static and the builder copies the right
subset per save.

    gen_constrain.py <final/constrain.ts> <out-dir>

Writes c6 c8 c9a c9b c12 c14 c15 c16 c17 .ts files. Asserts c17 is byte-identical to
the input.
"""

import sys
from pathlib import Path

src_path, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
lines = src_path.read_text().split("\n")


def at(anchor: str) -> int:
    hits = [i for i, l in enumerate(lines) if l == anchor]
    assert len(hits) == 1, f"anchor not unique ({len(hits)}): {anchor!r}"
    return hits[0]


def seg(a: int, b: int) -> list[str]:
    return lines[a:b]


# ── slices of the final file ────────────────────────────────────────────────────────
i_head_end = at("import './kinds/E'")
KIND_IMPORT = {k: at(f"import './kinds/{k}'") for k in "CMTFHE"}
BASE_IMPORTS = [
    "import { pointOf, type Constraint, type Drawing, type Point } from './drawing'",
    "import { ERROR, MOVABLE } from './kinds/registry'",
]
assert lines[0] == BASE_IMPORTS[0] and lines[1] == BASE_IMPORTS[1]

i_varsof = at("function varsOf(drawing: Drawing, constraint: Constraint): Point[] | null {")
HEADER = seg(i_head_end + 1, i_varsof)  # blank line + the Chapter VIII comment block

i_relax = at("export function relax(drawing: Drawing, passes = 400): Drawing {")
MID = seg(i_varsof, i_relax)  # varsOf .. errorsOf .. sensitivity .. solve .. STEP
i_relax_end = i_relax + next(k for k, l in enumerate(lines[i_relax:]) if l == "}")
RELAX_TODAY = seg(i_relax, i_relax_end + 1)

i_op = at("// ONE PASS METHOD, Chapter VIII.")
AFTER_RELAX_GAP = seg(i_relax_end + 1, i_op)
i_var = at("type Variable = { id: string; points: string[]; constraints: Constraint[] }")
OP_HEAD = seg(i_op, i_var)
VAR_LINE = [lines[i_var], ""]
i_mov_fn = at("function movable(constraint: Constraint, ids: string[]): boolean {")
i_varsof_cmt = at("// A fixed point is not a variable, so an instance pinned at both ends is not one")
MOVABLE_FN = seg(i_mov_fn, i_varsof_cmt)
i_free_cmt = at('// "Suppose that some variable can be found which has so few constraints applying to')
VARSOF_FN = seg(i_varsof_cmt, i_free_cmt)
i_settled = at("const SETTLED = 1e-9")
FREEDOMS_FN = seg(i_free_cmt, i_settled)
TAIL = seg(i_settled, len(lines))


def imports(kinds: str) -> list[str]:
    return BASE_IMPORTS + [f"import './kinds/{k}'" for k in kinds]


# Before "an instance moves as one thing", relaxation nudged single points.
def relax_prevariables(fixed_guard: bool) -> list[str]:
    guard = ["      if (point.fixed) continue"] if fixed_guard else []
    return (
        [
            "export function relax(drawing: Drawing, passes = 400): Drawing {",
            "  if (drawing.constraints.length === 0) return drawing",
            "  let current = drawing",
            "  for (let pass = 0; pass < passes; pass++) {",
            "    let worst = 0",
            "    for (const point of current.points) {",
        ]
        + guard
        + [
            "      const rows = current.constraints",
            "        .filter((c) => (MOVABLE[c.kind] ?? []).some((i) => c.vars[i] === point.id))",
            "        .flatMap((c) => sensitivity(current, c, [point.id]))",
            "      if (rows.length === 0) continue",
            "      const raw = solve(rows, 2)",
            "      if (raw.some((v) => !Number.isFinite(v))) continue",
            "      const reach = Math.hypot(...raw)",
            "      const scale = reach > STEP ? STEP / reach : 1",
            "      const dx = raw[0] * scale",
            "      const dy = raw[1] * scale",
            "      worst = Math.max(worst, Math.abs(dx), Math.abs(dy))",
            "      current = {",
            "        ...current,",
            "        points: current.points.map((p) =>",
            "          p.id === point.id ? { ...p, x: p.x + dx, y: p.y + dy } : p,",
            "        ),",
            "      }",
            "    }",
            '    // "Since each step makes some net reduction of total error, there will be',
            "    // monotonic decrease of error and thus stability is assured.\" So stopping when",
            "    // nothing moved any more is safe.",
            "    if (worst < 0.01) break",
            "  }",
            "  return current",
            "}",
        ]
    )


# At "an instance moves as one thing": the variables loop, before the one pass method
# exists to be mentioned or to stamp `settled` on the way out.
RELAX_VARIABLES: list[str] = []
for l in RELAX_TODAY:
    if l == "  // Instances move as a unit here for the same reason they do in the one pass":
        RELAX_VARIABLES.append(
            "  // Instances move as a unit. Nudging an instance's origin without its handle")
        continue
    if l == "  // method below. Nudging an instance's origin without its handle turns and resizes":
        RELAX_VARIABLES.append("  // turns and resizes")
        continue
    if l == "  return { ...current, settled: { method: 'relaxation', passes: used, ordered: 0 } }":
        RELAX_VARIABLES.append("  return current")
        continue
    RELAX_VARIABLES.append(l)


def emit(name: str, parts: list[list[str]]) -> str:
    text = "\n".join(l for part in parts for l in part)
    (out_dir / name).write_text(text)
    return text


out_dir.mkdir(parents=True, exist_ok=True)

emit("c6.ts", [imports("C"), HEADER, MID, relax_prevariables(False), [""]])
emit("c8.ts", [imports("CM"), HEADER, MID, relax_prevariables(False), [""]])
emit("c9a.ts", [imports("CMT"), HEADER, MID, relax_prevariables(True), [""]])
emit("c9b.ts", [imports("CMTF"), HEADER, MID, relax_prevariables(True), [""]])
emit("c12.ts", [imports("CMTFH"), HEADER, MID, relax_prevariables(True), [""]])
emit("c14.ts", [imports("CMTFHE"), HEADER, MID, relax_prevariables(True), [""]])
emit("c15.ts", [imports("CMTFHE"), HEADER, MID, RELAX_VARIABLES, [""], MOVABLE_FN, VARSOF_FN])
FREEDOMS_UNEXPORTED = [
    l.replace("export function freedoms", "function freedoms", 1) for l in FREEDOMS_FN
]
emit("c16.ts", [imports("CMTFHE"), HEADER, MID, RELAX_TODAY, AFTER_RELAX_GAP, OP_HEAD,
                VAR_LINE, MOVABLE_FN, VARSOF_FN, FREEDOMS_UNEXPORTED, TAIL])
c17 = emit("c17.ts", [imports("CMTFHE"), HEADER, MID, RELAX_TODAY, AFTER_RELAX_GAP, OP_HEAD,
                      VAR_LINE, MOVABLE_FN, VARSOF_FN, FREEDOMS_FN, TAIL])

assert c17 == src_path.read_text(), "c17 must be byte-identical to the final file"
print(f"9 files in {out_dir}; c17 == final verified")
