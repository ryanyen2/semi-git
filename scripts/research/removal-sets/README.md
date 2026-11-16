# Removal sets: research prototype

A stand-alone prototype of the model in `docs/design/2026-11-16-removal-sets.md`.
It does not import `ugh`. It reads a git history, rebuilds it line by line, and answers
"what would it take to make this request gone".

Nothing here is wired into the tool. The scripts are kept so the measurements can be rerun.

## Setup

```
cd scripts/research/removal-sets
tar xzf exp-bikecount.tgz && tar xzf exp-footfall.tgz   # the two study projects with their history
```

The `.tgz` files and the unpacked folders are local data (ignored by git). They are clones of the
study projects in `web/public/bundles/`, one commit per request, matching `docs/study/answer-key.json`.

Other inputs: this repository's own history (`editor/vscode/src`, and `sgt/` before the rename
commit `e4783a66`), a checkout of vibe-widget for the Python runs, `ruff` on the path, and the
extension's `node_modules` for the TypeScript compiler.

## What each script does

| script | what it checks | result when last run (2026-11-16) |
|---|---|---|
| `weave.py` | the prototype itself: every line with the commit that wrote and removed it, the program as a function of which commits are in | every recorded state rebuilt byte for byte on seven histories |
| `compile_steps.py` | take each commit out of `editor/vscode/src`; the TypeScript compiler is the judge | 51 of 51 compile; 40 of 40 random sets (`MULTI=40`) |
| `check_py.py <bikecount\|footfall> [n]` | every on/off combination of the 12 requests; the project's `check.py` is the judge | bikecount 4,096 of 4,096; footfall 1,000 of 1,000 sampled |
| `check_ruff.py`, `check_import.py`, `check_smoke.py` | the same loop on vibe-widget and `sgt/`, judged by ruff, by importing every module, by running the tool | vibe-widget 79 of 79; `sgt/` 82 of 82 and 41 of 41 |
| `check_tests.py` | tests tracked as steps, the remaining tests as judge (`ALSO=tests/entities,tests/store`) | 15 of 15 commits; slow |
| `nonlinear.py` | edit while a commit is out, then bring it back; git's merge is the answer | 77 of 77 saved, 76 of 76 brought back |
| `bring_back.py`, `bring_back_py.py` | take out, edit what came back, bring back, with a judge | 8 of 8 (TypeScript), 29 of 29 (`sgt/`) |
| `compose.py` | do per-commit results combine without compiling again | 59 of 60 |
| `untangle.py` | can a text merge separate two requests that rewrote the same lines | 0 of 145 |
| `alt_rule.py` | the two defaults compared (`TOUCH_KEEPS=1` is the chosen one) | see the design note |
| `collateral.py`, `whole_sets.py` | what a removal set takes from other requests | see the design note |
| `demo_b.py` … `demo_b5.py` | what the tool would say, five versions | output in `fresh-users/` |
| `quiz2.py` and the `quiz*` files | reading test of the rule | see the design note |

Run from the repository root, for example:

```
python3 -B scripts/research/removal-sets/check_py.py bikecount 200
TOUCH_KEEPS=1 python3 -B scripts/research/removal-sets/demo_b5.py
MULTI=40 uv run python scripts/research/removal-sets/compile_steps.py
uv run python scripts/research/removal-sets/check_ruff.py ~/repos/vibe-widget src/ vibe_widget HEAD
uv run python scripts/research/removal-sets/check_smoke.py "$PWD" "" sgt e4783a66^ 4
```

## `fresh-users/`

The five briefs given to simulated first-time users (`fresh-user-brief*.txt`) and the prototype
output each one contained (`demo-b*-output.txt`). The fifth is the current one.
