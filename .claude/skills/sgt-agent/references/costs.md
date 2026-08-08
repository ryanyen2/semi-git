# What each sgt read costs, and why the numbers matter

A cost table in prose goes stale silently, and a stale one is worse than none because it teaches
confident wrong choices. So this records the measurements, the conditions, and the command that
re-takes them.

## Re-taking the measurements

```bash
python -m scripts.measure_agent_cost              # every read, on the current repo
python -m scripts.measure_agent_cost --scaling    # growth against synthetic 10/30/60-commit repos
```

Token counts are bytes divided by four, the usual approximation. Nothing here depends on precision;
what matters is the ratio between reads and which ones stay flat.

## On this project's own repo (290 commits)

| read | tokens | ms |
|---|---|---|
| `sgt_recall` | ~10 | 210 |
| `sgt_drift` | ~10 | 280 |
| `sgt_advanced_fsck` | ~15 | 3300 |
| `scripts/sgt_brief` | ~80 | — |
| `sgt_now` | ~530 | 910 |
| `sgt_show` | ~720 | 570 |
| `sgt_log` | ~1400 | 260 |
| `sgt_status` | ~3300 | 400 |

Latency is dominated by whether that call is the one that mines the working tree. The first sgt call
in a session pays for mining (seconds); later ones do not. Do not read the millisecond column as a
per-call cost or you will avoid a second read that is nearly free.

## Growth, which is the part that decides the guidance

| commits | `sgt_brief` | `sgt_now` | `sgt_log` |
|---|---|---|---|
| 10 | ~23 tokens | ~326 | ~1106 |
| 30 | ~24 tokens | ~330 | ~1099 |
| 60 | ~24 tokens | ~330 | ~1118 |

All three are flat, which is what makes them safe to reach for on a repository of any age. `sgt_log`
is flat because it is windowed to 30 ops over MCP and reports `truncated` when it clipped.

The contrast is the grid, which is why it is not an MCP tool: `grid_view` measured ~1,470 tokens at
10 commits, ~4,620 at 30, ~6,480 at 60, and about **129,000** on the 290-commit repo. That growth is
inherent — a grid is one cell per (feature, commit) and a UI needs every cell to draw it. Nothing is
wrong with the projection; it is simply shaped for a renderer rather than a reader.

## Two payload decisions worth knowing about

Both came out of these measurements, and both are the same judgement.

`sgt_show` omits its op-id list unless you pass `include_ops`. Ids are 64 characters each and no
surface prints them, so on a large feature they were most of the payload. `op_count` carries the fact.

`consequences.affected_symbols` is capped, with `affected_symbol_count` beside it. Reverting a large
feature moves around a hundred symbols; uncapped that was 5.3 KB of a single response, and the
actionable part of a consequence is the magnitude (`removes`, and how many of those are work built on
top), not the roll call.

In both cases the option that was rejected is the interesting one: a *silent* slice. A caller reading
five of forty ids has no way to know the rest exist, whereas an absent field is unmistakably absent
and a count next to a short list is unmistakably a sample. When you find yourself capping something,
ship the count with it.
