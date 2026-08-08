# The forecast band — giving anticipated work a place in the graph

sgt could draw what you did with real care, and had nowhere to draw what you were about to do. So
anticipated work leaked out of the graph as chrome: a dashed underline with a `+1` badge for pending
plan steps, and a separate 9px dashed stub with a `+4` badge for uncommitted edits. Two shapes, two
units, two places, one idea.

This doc records the audit that found that, the reason it happened, and the design that replaces it.
Every claim below was checked against live output on this repo, not read off the source.

## What the surfaces actually said

### Three sibling views reported three different sizes for one repository

Run in one process, against one store, seconds apart:

| command | reported |
|---|---|
| `sgt log` | 145 save(s) · **44** feature(s) |
| `sgt log --map` | **332** save(s) · **84** feature(s) |
| `sgt status` | **154** feature(s) |

The same nouns silently carried three denominators: saves that carried tracked work, all commits, and
features that *led* a save versus features that exist. `--map`'s number was worse than wrong, it was
*unstable* — it counted visible rows, so folding a subsystem shrank the reported size of the repo.

A comment already in `graph.py` (`save_count`) warns against exactly this contradiction, and fixes
only one of the two numbers.

### `+N` meant five unrelated things, in one product, often on one screen

| where | unit | meaning |
|---|---|---|
| `+13` on a save row | features | touched features not shown |
| `+245 more` in `sgt status` | files | changed files elided |
| `+N older save(s)` | saves | history past the render cap |
| `+4` above a ghost car | edits | uncommitted ops that would land on save |
| `+1` beside a lane | plan steps | pending steps predicted for this lane |

Nothing marked which. The last two sat inside the same graph, three pixels apart, in the same accent
colour, at the same font size — one counting edits, one counting steps.

### The good preview grammar already existed and was not used

The webview's "Focus & Morph" overlay for `revert` deep-dims the field, relights only affected lanes
by role (target / blast / foundation), rewrites each lane's count to an explicit `N → M`, and morphs
emptying lanes into ghosts. That is a real after-effect. The plan and save previews never used any of
it.

### Unfinished polish

`planStepEnterStagger` assigned every newly-arrived plan step an entry index on each render, and
nothing read it. The terminal had the same split: plan work appeared as a `◇ planned:` chip on the
checkpoint line *below* the lane, outside the density-bar grammar every other unit of work lives in.

## Why it degenerated into a badge

Not carelessness — a missing region.

sgt holds one encoding contract firmly: **hue is identity; status is carried by glyph, dash, or
opacity, never by a second hue** (`sgt/tui/color.py`, `editor/vscode/src/color.ts`).

But the graph's x-axis has a hard domain, `c0` to the last commit, so its right edge *is* the present.
Anything anticipated has no coordinate. A car for it had nowhere to sit, and the only space left was
chrome. Both bad encodings are what you get when a real concept has no place to stand.

> Hue was already spoken for by identity. Position was already spoken for by time. Nothing was spoken
> for by *not yet* — so give it a region, not a decoration.

## The design

Reserve columns to the right of a `now` rule. Left of it happened; right of it has not. Anticipated
work then renders as an ordinary **car** — same rounded rect, same identity hue, same three-tier label
rule as history — so it reads with the vocabulary the reader already learned from the left of the rule.

The band is a faint wash, not a few floating marks, so it reads as a *place*. That region plus one
word (`next`) is the whole explanation, which is what lets every individual card drop its caption.

The band appears only when something is pending, so an idle repo keeps exactly the axis it had.

### Two kinds of "not yet", separated by stroke — never by a second hue

| card | stroke | motion | says |
|---|---|---|---|
| save ghost (`.gcar-pending`) | `3 3` dash, filled | slow pulse | real edits on disk now; they land on the next save |
| plan ghost (`.gcar-plan-ghost`) | `1.5 2.5` dash, hollow | still | a step someone intends; no code exists yet |
| stack card | + offset back edge | still | more behind this one, said in shape |

Motion carries that distinction rather than decorating it: **only the save ghost pulses, because only
it describes work that already exists.** A plan ghost is still, because nothing is happening yet.

### What replaces `+1`

A plan step already has a name and a predicted footprint; the badge threw both away and kept the
arithmetic. The card is therefore *named*, rationed by the same three tiers `renderCars` already uses:

| tier | shown | carries |
|---|---|---|
| ambient | always | the card itself — dashed, identity hue, in the band. Shape alone says "more is coming here" |
| inline | when it fits | the step's title, inside the card, exactly where a checkpoint's intent goes |
| on demand | hover / click | rationale + predicted footprint symbols; click selects the step |

The click target matters on its own: a plan card in the graph and its card in the inspector now select
one thing. The old badge was inert.

### Crowding sheds cards; it never shrinks them into stubs

A forecast has only its label to identify it — history can lean on position in time. Truncating a
title to six glyphs (`Reserv…`) leaves the reader exactly where `+1` did. So `ganttGeom` reports how
many slots survive at a nameable width (`GANTT.ghostW`, ~12 glyphs) and the renderer collapses the
rest into the stack card.

This was caught by testing, not by reasoning: the first implementation looked right and rendered
`uncomm… | Reserv… | Draw g…` at an ordinary pane width.

One ordering rule holds everywhere: **a bare count is never the reader's only information.** While no
card is named, the stack shows a *name*; the count appears only once names are already on screen.

### The terminal twin

Same band in `sgt log --map`: columns carved out of the density bar, capped at 40% so a forecast can
never crowd out the history it hangs off, past a `┊` rule.

```
  ▾ Architecture & Design                6 feature(s)
    ● 004f48b6  Project Bootstrap      ▃▅█▂·▇▅  ┊ ◇ unify the… +2
    ● m1999a0e  Semantic Versioning…   ▂▄▆█▃
    ● 0575f655  Operation Store…       ·▁▃█       ┊ ◇ add now-rule…

  ▁▂▃▄▅▆▇█ = edit density   ·   past ┊ = planned, not built yet
```

One deliberate divergence from the webview: a terminal has no hover, so a collapsed card cannot defer
its count to a tooltip. It rides on the named card (`◇ unify the… +2`), because here the count is
recoverable nowhere else.

The legend clause appears only when a lane actually has a forecast — same reason the rail's legend
names only the topology glyphs it drew. A legend that describes absent marks teaches the reader that
the header is not about what they are looking at, and they stop reading it.

## What shipped

Verified by `node editor/vscode/dev/smoke.js` (22 assertions) plus `tests/tui/`, `tests/golden/`,
`tests/cli/`, `tests/test_api.py`, `tests/test_rail.py`, `tests/test_graph_layout.py` — all green.
One golden snapshot was refreshed; its diff *is* the finding — that fixture has 7 saves, 4 of them
tracked, and the old header showed only "4 save(s)".

- Forecast band in the webview map: reserved geometry (`ganttGeom(forecastCars)`), `now` rule, band
  wash, ghost cards in the car grammar (`laneForecast` / `renderForecastCars`).
- Retired the plan underline and both `+N` badges; plan cards bound to `selectPlanStep`.
- Wired the dead `planStepEnterStagger` into a 55 ms staggered entry, gated on
  `prefers-reduced-motion`.
- Responsive shedding into a stack card, asserted at 420px as well as 900px.
- Terminal band in `sgt log --map` (`_forecast_band`), with a conditional legend clause.
- One shared header rule (`_history_header`) so `log`, `--map` and `status` cannot disagree; `--map`
  counts features, not rows.
- Repaired `dev/smoke.js`, which had been failing at committed `HEAD` on a stale DOM shim (it minted
  a fixed list of element ids and died on `viewSeg`, long after that control shipped).

## Still open

- The **episode rail** still renders plan work as pseudo-rows with `plan` in the position column — a
  third encoding for anticipated work, not yet folded into the band grammar.
- The rail's **lane gutter breaks down at scale**: 43 recurring features produce ~16 columns of `│`
  whose ownership is unreadable without the hover a terminal does not have.
- `sgt log` still opens with a dim stale-cache warning above its content — the most consequential
  line rendered in the least visible style.
- The remaining three `+N` senses (features, files, older saves) are still unqualified and should each
  name their unit.

## Note

`dev/smoke.js` used a literal `NUL` as a composite-key separator, which made git treat the file as
binary and made `grep` fail silently on it. Changed to `|`; the key is local to one function and no
behaviour depends on it. Pre-existing, flagged here because it was outside the brief.
