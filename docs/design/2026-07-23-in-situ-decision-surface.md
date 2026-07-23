# In-situ decision surface — active design doc

**Status:** living. Started 2026-07-23. Owner: design pass on the `sgt` TUI + VS Code visualization.

> **The one goal.** Every pixel a surface spends should help the user answer two questions:
> *where am I right now,* and *what is the next decision.* Nothing else earns its place.
> When a coding agent drifts from the plan, the surface should **show** it before the user acts —
> not wait for the user to think to run a `drift` query. Encode with channels, not chrome:
> subtle, in-situ, one more mark on the spine we already draw, never a new panel.

This document is the running record of that pass: the substrate as it exists, a friction log from
walking real workflows through the real code, the structural findings, the proposals, and a
validation log that checks whether each proposal actually shows what we claim. It is meant to be
edited as we learn. See the changelog at the end.

---

## 0. Method

- Read the real render code, not the docs' idealized output: `sgt/api.py` (`grid_view` and the
  plan/drift/trust views), `sgt/tui/graph.py` + `sgt/tui/app.py` (terminal), and
  `editor/vscode/media/workbench.{js,css}` + `src/workbench.ts` (webview).
- Ran the surfaces against this repo's own store (1882 ops, 219 commits, 24 features) to see what a
  user literally sees. Terminal captures are quoted verbatim below.
- Judge each surface against the one goal, per workflow. "Enough" means *the next decision is
  legible without a second command*; "overwhelming" means *a channel is spent on something that
  doesn't change a decision.*

---

## 1. The substrate

### 1.1 One data model, `grid_view` (`sgt/api.py:796`)

Every surface renders the same projection. It is a **lane × commit grid**:

| field | meaning | axis |
|---|---|---|
| `commits` | `{sha, subject, index}`, oldest→newest | **x = time** |
| `features` | `{feature_id: {label, op_count}}` | **y = identity (a lane)** |
| `cells` | one per (feature, commit): `op_ids, op_count, kinds, fidelity` | a filled cell |
| `ghosts` | pending plan predictions: `{feature_id, session_id, step_index, title, known_feature}` | **planned, no code yet** |
| `partial_commits` | commit indices where mining dropped ops it couldn't reduce | **fidelity** |

The important thing: **`ghosts` and `fidelity` are already computed.** The plan's forward-looking
intent and the "this commit isn't fully represented" signal are in the model. Whether a surface
*draws* them is a separate question — and the answer differs by surface (§3, F1).

### 1.2 The shared encoding vocabulary (the channels already in use)

The project has a real, disciplined channel language. The rule is stated in `sgt/tui/color.py:5`
and honored across surfaces:

> **Hue is identity. Status is a glyph + dim + texture, never a hue.**

| channel | encodes today | where |
|---|---|---|
| **hue** (OKLCH golden-angle hash) | *which feature* — same hue in TUI, gutter, webview | everywhere |
| **brightness / shade** | op **density** inside a car | `_shade`, `.gcar-cell` opacity |
| **bracket / dash** `[ ]` vs `( )` / `‑ ‑` | segment **tier** (coupled vs thematic claim) | `_TIER_BRACKETS`, `.gcar-thematic` |
| **bold / 2px frame** | the lane's **big event**, or **selected** | `render_car`, `.gcar-selected` |
| **dim / veil / opacity** | **past frontier / future / secondary** | `_DIM`, `.future-veil`, `.beyond` |
| **glyph** | **kind**: `●` feature `◈` meta `▸` subsystem `◦` symbol; `✦N` rewind pts; `⋔` fork; `⚠` warn | `_KIND_GLYPH`, `GLYPH` |
| **position x / y** | **when** / **which feature** (grouped into subsystem swimlanes) | both layouts |
| **digit** | the checkpoint `@n` handle (the revert token) | `render_car` |

This is the palette I must extend. **Every proposal below adds a mark to this spine; none adds a
panel.** That is the minimalism constraint, and the codebase already earned it.

### 1.3 Three surfaces, and where they diverge

| capability | CLI `sgt log` (terminal) | TUI (`SgtTui`) | VS Code webview |
|---|---|---|---|
| lane×commit Gantt of features | ✅ `render_graph_lines` | ✅ GraphScreen (`g`) | ✅ `renderGraph` |
| episode rail (vertical git-log) | ✅ `--rail` | ✅ EpisodeScreen (`l`) | ✅ `renderRail` |
| feature tree (selectable) | ✅ `--tree` | ✅ main table | ✅ inspector |
| frontier scrub (fold through time) | `--at` | ✅ ←/→ in GraphScreen | ✅ draggable playhead |
| dependency-aware revert closure | — | ✅ FrontierScreen (`f`) | ✅ selection card union |
| **plan predictions (ghosts) on the grid** | ❌ **computed, never drawn** | ❌ **never drawn** | ✅ `.gbar-plan` dashed underline + `+N` |
| **drift marks on the grid** | ❌ | ❌ | ✅ `.gbar-drift` solid identity ring |
| **fork marks on the grid** | ❌ (only in `--summary` scalar) | ⚠ status-band count only | ✅ `⋔` gutter badge, clickable |
| **plan session progress** | ❌ | ❌ | ✅ titlebar `.plan-chip` ring `matched/total` |
| **"plan said X, landed on Y"** | ❌ | ❌ | ✅ plan-card predicted→matched dot+arrow |
| **agent trust queue** (`trust_view`) | ❌ (only `sgt advanced review-queue`) | ❌ | ❌ |

**The headline structural fact:** the webview is a full generation ahead of the terminal on exactly
the thing the goal cares about — plan/drift/fork. The terminal daily-loop user (the *primary*
user of a git-shaped tool) is blind to it on the grid. `graph.py`'s own header still claims the two
are "faithful ports, kept behaviour-parallel on purpose." For the base Gantt, true. For the
plan/drift/fork layer, **no longer true.**

---

## 2. Workflow walkthroughs — the friction log

Each walkthrough states the decision the user is actually trying to make, then what the surface
gives them, then the gap.

### W1 — The solo daily loop (`save` → glance → continue)

**Decision:** *did my last save land what I meant, and is anything off?*

What they get:
- `sgt save -m …` prints `✓ save <sha>: N op(s)` and, if a plan session is active, per-step
  fulfilment lines and a terse `drift: <12-char op ids>` line (`porcelain.py:238`).
- `sgt log --summary` prints counts + `oracle` + `⚠ drift: <paths>` / `✓ in sync`.

Friction:
- The `save` **`drift:` line is a list of truncated op ids** — `drift: a776620b9b56, 3f...`. That is
  an identifier, not information. It says *that* something is off-plan but nothing about *what*
  (which symbol, which file, how much). The user cannot decide from it; they must go query.
- `--summary`'s `⚠ drift` is a **different drift** (bytes-on-disk vs ideal; §3 F2). A daily user
  reasonably reads the two "drift"s as the same thing. They are not.
- **Verdict:** under-informative at exactly the moment of the daily glance. This is where the
  "since last save" decision line (§4 P2) belongs.

### W2 — Remove one thing from a tangled agent edit (`revert`)

**Decision:** *if I pull this function, what else comes with it, and is that acceptable?*

What they get (the good part):
- TUI `f` (FrontierScreen) and the webview selection card **both do dependency-aware closure**:
  `foundation` rows are read-only prerequisites; `blast`/`carry` rows are toggleable dependents;
  a live "removes N dependent(s) · keeps M · K foundation" readout. This is genuinely the
  "chunks connected because of dependencies" the goal asks for — it exists.

Friction:
- It is **reactive and revert-framed.** You reach it by selecting a row and pressing `f`. Nothing
  says "there are 3 connected chunks here worth reviewing." The dependency structure is shown as a
  **flat checklist of op-ids** (`table.add_row(marker, oid[:12], bucket)`), not as connected
  chunks — you see *that* op X is a dependent, not *what depends on what*. The unit the goal wants
  (accept/reject a connected chunk) is representable but not presented.
- On the CLI grid there is **no closure preview at all** — revert is a bare command; the blast
  radius only appears after you run `--emit` or in the other two surfaces.
- **Verdict:** the machinery is right; the framing and the shape are wrong. See §4 P3.

### W3 — The agentic loop: plan → agent runs → **it drifts** → decide  *(the headline)*

**Decision:** *the agent touched a dozen symbols; which of that was my plan, which is extra, and
what do I accept?*

The data exists and is rich:
- `plan_view` (`api.py:1044`): each session's steps, matched vs pending, checkpoint match groups.
- `drift_view` (`api.py:1099`): every op **no active plan step predicted** — "what extra happened."
- `trust_view` (`api.py:1129`): every op carrying **session/agent attribution or drift** not yet
  reviewed, **grouped by provenance** (session name / agent / `"drift"`). This is *exactly* the
  "the agent returned code, review it" surface.
- `grid_view.ghosts`: the plan steps with **no code yet**.

What each surface shows:
- **Webview:** genuinely good. Pending plan steps → dashed accent underline `.gbar-plan` + `+N`
  under the predicted lane. Drift op → solid identity ring `.gbar-drift` on the lane's bar.
  Unplaced drift → titlebar `⚠ N unplaced drift`. Plan card shows predicted→matched dot+arrow
  ("plan said X, landed on Y") *only when they differ.* Session progress ring in the titlebar.
- **Terminal + TUI:** **none of it.** The grid draws the same lanes with no plan underline, no
  drift ring, no ghost tip. `save` gave a truncated op-id list and moved on.

Friction (even in the webview, the good case):
- **Drift has no verb.** A drift ring tells you an op is unplanned. But there is no
  *accept this drift* / *reject this drift* action attached to it. Your only tools are revert the
  whole feature or rewind a checkpoint. "This extra work is fine, absorb it into the plan" and
  "this extra work is wrong, drop just it" are both unreachable as single gestures.
- **Drift-in-a-known-feature is nearly silent.** Only *unplaced* drift (no lane) gets the loud
  titlebar chip. Drift that landed inside an existing feature is a thin ring on a bar that's
  already busy with cars — easy to miss, which is the opposite of "show it before they act."
- **`trust_view` is drawn by no surface at all.** The one projection built for "review the agent's
  landing, grouped by who did it" is CLI-only (`sgt advanced review-queue`). The review *is* the
  agentic-loop decision, and it has no visualization.
- **Verdict:** the webview *shows* drift but doesn't *resolve* it; the terminal doesn't even show
  it. This is the single biggest gap against the stated goal. §4 P1 + P3.

### W4 — Remote collaboration: `sync` → forks

**Decision:** *my push was rejected; what actually conflicts, and what can I merge without thinking?*

What they get:
- `sync_view` / `forks_view` isolate the conflict to same-symbol forks and merge everything else.
  `open_fork_count` is the "divergence-as-state loudness signal."
- Webview: `⋔` gutter badge on the forked lane, clickable → resolve; titlebar `⑂ N unplaced fork(s)`.
- Terminal: `sgt log --summary` → `⚠ N OPEN FORK(S) — run sgt forks`. TUI status band: same count.

Friction:
- On the terminal grid (the surface you're most likely staring at post-sync) **the fork is
  invisible** — you have to switch to `--summary` to even learn a fork exists, then to `sgt forks`
  to see which symbol, then `sgt resolve`. Three surfaces for one decision.
- The fork is shown as a **count**, not a **location**. "1 open fork" doesn't tell you *foo() in
  main.py* until two more commands. The webview does better (badge on the lane) — again the
  terminal is behind.
- **Verdict:** the conflict is correctly *narrowed*; it is not correctly *placed* on the terminal
  spine. §4 P1 (the `⋔` badge ports cleanly to the terminal gutter).

### W5 — Propose / review / land

**Decision (reviewer):** *which features are ready to accept, independently?*

What they get: `sgt propose … --subset <feature>` accepts feature-by-feature; webview arms a
target-pick mode for merge/move. Good.

Friction:
- The "accept feature by feature" story is a **CLI subset flag**, not a visual accept/reject on the
  grid. The reviewer's mental model — "tick the features I trust, land those" — maps perfectly onto
  the lane roster we already draw, but there is no checkbox-on-lanes gesture. (This is P3's
  review-mode overlay again, from the reviewer's side.)
- Known limit worth surfacing *in situ*: two features adjacent in one file can lose the separator
  when only one is subset-landed (`workflows.md` §5). Nothing on the grid warns that two lanes are
  file-adjacent. A candidate for a subtle adjacency mark, but low priority.

---

## 3. Cross-cutting findings

**F1 — The terminal grid is a generation behind the webview.** `grid_view` carries
`ghosts`/`fidelity`/`partial_commits`; `render_graph_lines` reads none of them. The webview renders
all the plan/drift/fork language. Porting those marks to the terminal is the highest-value,
lowest-risk change in this doc — it's additive, reuses the established channel discipline, and
closes W3/W4 for the primary user. *(→ P1)*

**F2 — Two "drift"s share one word, and both render as a wall.**
- **Tree drift** (`fsck_tree`, `status.drift`): `code(current_ideal)` ≠ HEAD bytes. Remedy: `sgt get`
  (absorb) or `sgt save` (enforce). Shown as `⚠ drift: <paths>`.
- **Plan drift** (`compute_checkpoint.drift_op_ids`, `drift_view`): ops no plan step predicted.
  Remedy: accept into plan, or revert. Shown as `drift: <op-ids>` in `save` / `sgt drift`.

Same word, different meaning, different remedy, both prefixed `⚠ drift`. A user cannot tell which
they're looking at. *(→ P6: rename one.)*

**Measured on this repo (2026-07-23):** `sgt log --summary` printed `⚠ drift:` followed by **~180
file paths inline** on one line, then `⚠ kept 72 unreproducible file(s):` with **another ~72 paths**
— a ~250-path wall of text as the "daily status." That is the "overwhelming" failure exactly: a
channel spent on data no decision reads. The remedy is aggregation, not enumeration —
`⚠ 180 path(s) out of sync · sgt fsck --tree to review` — the count is the signal, the list is a
drill-down, never the glance. *(→ P6 covers the naming; a new note under P2/P7 covers the wall.)*

**F3 — Review-of-agent-work is not first-class.** `trust_view` exists and is the natural home for
"the agent landed this, grouped by session; keep or drop each group." No surface draws it, and the
accept/reject-as-connected-chunk gesture the goal names doesn't exist as such (revert closure is
the nearest, and it's revert-framed). *(→ P3)*

**F4 — Nothing encodes the delta "since you last looked."** Both spines render all 219 commits with
equal weight. The user's actual question is about the *new* stuff. There is no recency channel and
no "here's what changed since your last save" line. *(→ P2, P4)*

**F5 — Label quality breaks the identity channel.** *(root cause partly traced 2026-07-23 — a real
label-pass bug, not just cosmetics.)* `sgt/lens/label.py:_clean_symbol_name` already drops
`__residue__`/`__anchor__` fold artifacts and `_fallback_label` uses it — so the offline fallback
path is *not* the culprit. Yet the rendered labels **start with** a raw `residue__::` token (see
capture: the 24-char window begins `residue__::forks_view`, so the raw label's first chars are that
token). Two tells: (a) the token is `residue__::`, not the `__residue__` that `_clean_symbol_name`
guards against, so even if that cleaner ran it wouldn't match; (b) a label that *starts* with a
member token means some non-fallback path (authored/ledger claim label, or a single-member cluster
defaulting to its member id) is writing raw member strings as `node.label`. This is for the
label-pass owner to trace — a render-time strip would be a band-aid over a data bug (CLAUDE.md §7).
Verbatim from `sgt log` on this repo:

```
● f-07506b03 frontier residue__:: HEA   [ …cars… ]
● f-047e2e4a residue__::forks_view re    [ …cars… ]
▾  HEAD  residue__::_assig 5 feat · 192 op
▾  HEAD  residue__:: HEAD  2 feat · 62 op       ← two swimlanes, indistinguishable
```

Labels are file/symbol salad truncated at 24 chars, dominated by `residue__::` and ` HEAD `
pseudo-symbol noise. **Hue is the identity channel, but the label is the *legend* for the hue** —
and the legend is unreadable. You cannot answer "which feature is this lane" from it. This quietly
defeats the whole "where am I." *(→ P7)*

**F6 — The grid legend points at removed verbs.** Verbatim footer of `sgt log`:

```
daily:  sgt graph  (fast, cached)  ·  sgt graph --refresh …  ·  sgt graph --focus <f-XXXX> …
operate: sgt revert <f-XXXX>  ·  sgt revert <f-XXXX>@<n>  ·  sgt intent show <f-XXXX>
```

`sgt graph` was folded into `sgt log` (U14); `render_graph_lines` still prints `sgt graph …` and
(in `--focus`) `sgt intent build`. The view that "explains its own encoding" explains it with
commands that no longer exist as primary verbs. Small, real, fixable now. *(→ P7)*

**F7 — Animation is barely used, and the goal explicitly asks for it.** Webview: 4 CSS transitions,
no `@keyframes`, the old FLIP/comet morphs were retired, `prefersReducedMotion()` exists but is
uncalled. TUI: zero motion. Transition is a free channel for *change over time* — precisely the
plan→actual convergence story. *(→ P4, P5)*

---

## 4. Proposals

Ordered by value/risk. Each is a channel addition to the existing spine. **Channel budget** —
what's still free after §1.2: *underline/underdot*, *a dedicated 1-col gutter left of the lane
marker*, *background band (a whole column or row)*, *border-style dashed/ring (webview)*,
*motion*. Proposals draw only from these; hue stays identity-only.

### P1 — Plan / actual / drift as three textures of one hue *(port + unify; do first)*

One reading rule, three textures, each in the feature's own hue so identity is never lost:

| state | terminal texture | webview (mostly exists) | meaning |
|---|---|---|---|
| **done as planned** | solid car `[n]` (today's default) | filled `.gcar` | code exists, matched a plan step |
| **planned, no code yet** (ghost) | dashed outline car at lane tip `⌐ ¬` / `[·]` dim dashed | `.gbar-plan` dashed underline + `+N` ✅ | intent with no op |
| **code, but off-plan** (drift) | **underlined** car / hatched fill `▒` at HEAD column | `.gbar-drift` solid ring ✅ | op no plan predicted |
| **partial fidelity** | trailing `…` on the commit tick | (add) | mining dropped ops here |

Terminal work = teach `render_graph_lines`/`segment_layout` to read `grid_view["ghosts"]` (draw a
dashed dim car at the predicted lane's tip) and the drift op-ids from `drift_view` (underline the
carrying car), plus port the `⋔` fork badge into the lane gutter and the fork *location* into
`--summary`. This is the F1 close. It is additive and testable against golden snapshots.

**Why this shape:** the goal's "what level of abstraction" question — the answer is *the segment/car
you already revert*, textured by plan-relationship. Not per-op (too fine, that's the drift ring's
job on hover), not per-feature (too coarse to see drift). The car is the unit the user already acts
on, so decorating it keeps action and information on the same object.

### P2 — The decision line (the proactive "where am I")

One line, above the grid and in the `save` output, that replaces "go run a drift query":

```
since last save · 3 features touched · 12 ops · ⚠ 4 off-plan · ⑂ 1 fork · oracle green
                                              └ underlined/amber, links to the review overlay
```

Built from `plan_view` + `drift_view` + `forks_view` + oracle — all already computed. This is the
single most direct answer to the goal: the surface tells the user the state *before* they act,
in one glance, and points at the one pending decision. On the daily `save` (W1) it turns the
truncated op-id line into a sentence a human can act on. It must fire on **read** (`sgt log`), not
only on `save` — validation V3 showed the agent-commits flow leaves `save` silent.

**Guard (from V1):** loudness scales with matcher confidence. If `matched==0` and off-plan≈all-new,
render the dim form — `plan signal weak · N new ops unmatched · sgt log --refresh to re-match` —
never `⚠ N off-plan`. A weak plan must not make the line scream. And name *what* via stripped
footprints (V2/P7), never `__anchor__::` tokens or op-ids.

### P3 — Dependency-chunked review overlay (accept/reject connected chunks)

A **mode on the existing grid**, not a new panel. Trigger it from the decision line ("4 off-plan →
review"). In review mode:
- The grid dims everything except the **unreviewed** cars (from `trust_view`, grouped by session /
  agent / drift).
- Each group is drawn as its **dependency closure** — the connected chunk — using the closure the
  FrontierScreen already computes: `foundation` ops are shown *anchored* (can't drop, drawn with a
  connector `├─`), `blast`/`carry` dependents grouped under the thing they depend on. So you see
  *what moves with what*, not a flat op list.
- Two gestures per chunk: **keep** (accept — mark reviewed) and **drop** (reject — revert the
  closure). Batched, transactional, "stop on first refusal" like the webview's `revertSelection`.

This is the goal's "instead of accept/reject one by one, chunks connected because of dependencies."
The dependency data (foundation vs blast/carry, the closure) already exists; the change is
*presentation* (group + connect + two verbs) and *entry point* (proactive, from the decision line),
not new kernel work.

### P4 — Recency channel + fade-in

- **Static:** the HEAD column (newest commit) gets a faint background band (`\x1b[48;2;…]` in the
  terminal, a `rect` fill in the webview) so "new" has a place your eye goes first. Ops newer than
  the last `save` render at full brightness; older ops keep today's density shading. Recency rides
  the *brightness* channel that's already about "how much happened," extended to "how recently."
- **Motion (webview, honor `prefers-reduced-motion`):** new cars **fade/slide in** on refresh
  (150ms), so a `save` that adds work *shows* the work arriving instead of silently re-laying-out.

### P5 — Ghost solidify transition (plan → actual convergence)

The one motion that earns itself: when a plan step is fulfilled, its **dashed ghost car morphs to a
solid car** (dashed→solid stroke, 340ms — the same easing the plan-progress ring already uses).
Convergence of intent and code becomes something you *watch happen*, which is far more legible than
a number ticking `matched 3/4 → 4/4`. Wire the existing-but-uncalled `prefersReducedMotion()` guard
so it degrades to an instant swap.

### P6 — Rename one of the two drifts

Keep **"drift"** for the tree-vs-ideal byte divergence (it's the established `fsck` word). Rename
the plan-relationship one to **"off-plan"** (or "extra"), matching the webview's existing "unplaced"
language. Touches labels/strings only: `save`'s `drift:` line, `drift_view`'s user-facing text, the
decision line. Removes a genuine ambiguity for zero risk.

### P7 — Fix the identity legend (labels) and the stale footer

- **Labels (F5):** strip `residue__::` and ` HEAD ` pseudo-symbol tokens from feature labels before
  display; prefer the *entity* name, fall back to a short dir + count, never raw path salad. The
  hue is only as useful as the word next to it. (Label generation lives in the lens/label pass;
  the display-time strip is the surgical version.)
- **Footer (F6): ✅ SHIPPED 2026-07-23.** `render_graph_lines`/`render_rail_lines` legend now prints
  `sgt log` / `sgt log --refresh` / `sgt log --focus` instead of the removed `sgt graph …`. Golden
  snapshot `cli_surface.json` regenerated (single-line diff); `tests/{golden,tui,…}` green (50
  passing). `sgt intent show`/`build` kept — those verbs still exist.

### Channel-discipline summary (the invariant every proposal keeps)

```
hue          → identity (feature)                          [never anything else]
brightness   → density, extended to recency                [P4]
texture      → plan-relationship: solid=done, dashed=ghost, underline/hatch=off-plan   [P1]
border/ring  → drift (webview) / selection                 [P1]
glyph        → kind + ⋔ fork + ✦ rewind                     [P1 ports ⋔ to terminal]
dim/veil     → past/future/reviewed                        [P3 dims the reviewed]
background   → recency (HEAD column) + review focus         [P2/P4]
motion       → change over time: ghost→solid, fade-in       [P4/P5, reduced-motion honored]
position     → when / which feature                         [unchanged]
```

---

## 5. Validation log

The goal insists on checking whether a change *actually shows the thing we expect.* I stood up a
live fixture (`scratchpad/driftfix`): a 2-file repo, `sgt plan intake "add rate limiting to login
and add a retry policy to check"`, then an agent-style edit that adds `rate_limited()` (on-plan),
extends `login()` (on-plan), and adds `track_event()` (off-plan telemetry). Results below.

### 5.1 Confirmed against the running tool

| finding | evidence (verbatim) |
|---|---|
| **F5 label salad** reproduces | `● f-047e2e4a residue__::forks_view re …`, two swimlanes both `▾  HEAD  residue__:: HEAD …` |
| **F6 stale footer** reproduces | `sgt log` footer literally prints `sgt graph … · sgt graph --refresh … · sgt graph --focus`, and `--focus` prints `sgt intent build` — both removed as primary verbs |
| **F2 wall** reproduces | `sgt log --summary` → `⚠ drift:` + ~180 paths inline, then `⚠ kept 72 …` + ~72 paths |
| **`sgt drift` is opaque** | human render is 8 bare lines `471860cf08db / 4b7f712b07e0 / …` — op-ids only, no file, symbol, or kind |
| **F1** (terminal draws no plan/drift) | `grep "ghost\|fidelity\|drift" sgt/tui/*.py` → only status-band *tree*-drift; grid render shows no plan/drift/ghost mark even with a live session |
| **P2 is buildable** | `sgt drift --full --json` **does** carry footprints (`auth.py::login`, `…::track_event`) + `kinds` — enough to name *what* drifted in a decision line |

### 5.2 Two findings the fixture *added* (they revise the proposals)

**V1 — The plan/drift signal is only as good as the matcher, and offline it is noise.** With no LLM
key, intake produced **1 step** (no decomposition) and the footprint matcher matched **nothing**:
`matched_count: 0`, and **all 8 ops — including the genuinely on-plan `login`/`rate_limited` work —
reported as drift.** So "off-plan" here was 100%, which is not a decision, it's noise.
→ **New design guard (applies to P1 + P2): when `matched==0` and drift≈all-new-ops, the surface must
read "no usable plan signal" (dim, single line), NOT "N off-plan!" (loud).** Loudness must scale
with the *matcher's confidence*, or a weak plan makes the whole grid scream. This is the difference
between "enough" and "overwhelming" for this exact feature.

**V2 — Pseudo-symbols leak into footprints too.** Drift footprints came back as
`auth.py::__anchor__::track_event`, `auth.py::__residue__::logout`. So P7's token-strip isn't just a
label cosmetic — the *decision line* (P2) would say "off-plan: `__anchor__::track_event`" unless the
same strip runs on footprints. P7 is a shared dependency of P2.

**V3 — In the realistic flow, `save` is silent.** Because the agent git-committed its own work,
`sgt save` mined-on-contact and reported `nothing to save`; the drift existed but surfaced **only**
via an on-demand `sgt drift`. This is the goal's premise, measured: the moment of the daily glance
shows nothing, and the divergence waits behind a command the user has to think to run. P2's decision
line has to fire on *read* (`log`), not only on *write* (`save`).

### 5.3 Still to prove (needs a build)

| # | claim | how |
|---|---|---|
| P1 | ghost = dashed tip car; drift = underlined car; `⋔` on forked lane — all in the terminal | render `render_graph_lines` against a fixture with a *real* (LLM-matched, or hand-seeded) plan so `matched>0`, so not everything is drift |
| P1 | reads correctly under `--no-color` and honors the V1 guard | golden snapshot with matched + off-plan + fork all present |
| P5 | ghost→solid morph fires on fulfilment and degrades under `prefers-reduced-motion` | webview fixture |

**Next build step:** the highest-value, lowest-risk slice is **P7 (footer + label strip) + P1's
fork-badge port + P2's decision line with the V1 guard** — all additive, all on the terminal spine,
all testable against the existing golden CLI snapshots. Defer the motion work (P5) until the static
marks read.

---

## 6. Open questions

1. **Where does "accept drift into the plan" write?** P3's *keep* gesture needs a home — does it
   confirm a plan match (`confirm_match`) even when the op matched no step, or mark-reviewed in
   `trust_view` only? Kernel-semantics question for the owner, not a display choice.
2. **Terminal ghost geometry.** A dashed car at a lane's *tip* competes for the same rightmost
   columns as real HEAD cars. Does the ghost get its own column past HEAD (a "future" gutter), or
   overlay the tip dimmed? Needs a render experiment (P1 validation).
3. **How loud should drift-in-a-known-feature be?** Underline is subtle by design (don't overwhelm),
   but W3 flagged it as *too* subtle. Resolve empirically once the fixture renders.
4. **Is the episode rail the better home for the decision?** The rail (newest-on-top vertical
   git-log) already reads more cleanly than the Gantt (validation §5 impression). The "since last
   save" delta might live most naturally as the top N rows of the rail. Worth prototyping both.

---

## Changelog

- **2026-07-23 v3** — first code slice shipped: **P7/F6 footer fix** (`sgt graph …` → `sgt log …`
  in the terminal grid + rail legend; golden snapshot regenerated; 50 graph/tui/golden tests green).
  Traced **F5** to a real label-pass bug (raw `residue__::` member token reaches `node.label` via a
  non-fallback path; `_clean_symbol_name` guards `__residue__` and never matches it) — handed to the
  label-pass owner rather than band-aided. Published an interactive visual pitch (Current→Proposed
  grid, ghost→solid morph, confidence-guarded decision line).
- **2026-07-23 v2** — ran a live plan+drift fixture (`scratchpad/driftfix`). Confirmed F1/F2/F5/F6
  against the running tool (incl. the ~250-path `--summary` wall and the op-id-only `sgt drift`).
  Added three fixture-driven findings: **V1** loudness must scale with matcher confidence (offline,
  everything drifts → the signal is noise); **V2** pseudo-symbols leak into footprints, so P7's
  strip gates P2; **V3** `save` is silent in the agent-commits flow, so the decision line must fire
  on read. Guard folded into P1/P2. Recommended first build slice: P7 + fork-badge port + guarded P2.
- **2026-07-23 v1** — first pass. Substrate mapped; 5 workflows walked; 7 findings; 7 proposals;
  validation log seeded. Key finds: terminal grid a generation behind webview on plan/drift/fork
  (F1); two-drifts naming collision (F2); trust queue unsurfaced (F3); label salad + stale footer
  reproduce on this repo (F5/F6).
