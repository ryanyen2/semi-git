#!/usr/bin/env bash
# Build the EasyOCR demo repo -- the prepared "your own repository" walkthrough for the
# closing interview of the CHI study (docs/study/interview-demo-easyocr.md).
#
#   scripts/demo/build-easyocr-demo.sh [<target-dir>]
#
# The target defaults to ~/repos/sgt-demo/EasyOCR and the script refuses to build over an
# existing one; pass FORCE=1 to wipe and rebuild.
#
# WHY THIS EXISTS
#
# Protocol v2's closing step ("Your own repository", web/src/participant/steps/Interview.tsx)
# asks the participant to open a repository of their own so the session ends on code they
# already care about. In practice most participants arrive without one -- no checkout on the
# study laptop, or one they are not allowed to show. The fallback has to be a repository the
# FACILITATOR opens on the shared screen, and it has to be warm before the participant is in
# the room: an interview is not the place to discover that a first `sgt log` takes forty
# seconds because nothing has been mined yet.
#
# WHY EasyOCR
#
# The demo's job is to show what sgt says about a codebase neither person wrote. That only
# works if the DOMAIN needs no explanation, because every minute spent explaining the project
# is a minute not spent looking at the graph. OCR is a picture of text going in and text
# coming out; "detection finds the boxes, recognition reads them" is the whole model, and it
# is a two-sentence preamble rather than an architecture lecture. EasyOCR is also the right
# SIZE (313 tracked files, 76 of them Python) and the right SHAPE: its top-level modules are
# named after the concepts -- detection.py, recognition.py, craft.py, easyocr.py -- so a
# participant can check sgt's answers against their own guess.
#
# WHY THE PIN
#
# EasyOCR is a live upstream. Feature ids, symbol counts, and every number in the walkthrough
# doc are functions of the mined history, so an unpinned clone would silently disagree with the
# doc the moment upstream lands a commit -- and it would disagree DIFFERENTLY for two
# facilitators building on two days.
PIN="363afb184047ce452e436f4224f3098422df872e"  # 2025-12-05 "Update README.md"
#
# WHY THE HISTORY IS CUT AT 2020-11-08, WHICH IS THE ONE DECISION IN THIS SCRIPT
#
# This is NOT a build-time optimisation. A full clone mines fine (619 commits, about 18
# minutes) and produces a graph that looks healthy from every angle a builder would check --
# and is useless for this demo. Measured on the full clone at this pin, 2026-09-01:
#
#   * 688 of 3,877 ops are excluded from the ideal, and 177 symbols that ops demonstrably
#     touched, whose files are still in the tree, are in no frontier.
#   * `sgt show "easyocr/easyocr.py::Reader"` answers "is not a known feature, checkpoint, op,
#     or symbol". So do detection.py::get_textbox, recognition.py::get_text,
#     utils.py::group_text_box, craft.py::CRAFT, and all of easyocr/DBNet/.
#   * Worse, the features that ARE visible are anchored on paths that no longer exist:
#     `sgt show "CRAFT Text Detector"` lists `easy_ocr/craft.py::CRAFT` and "last touched
#     2329d ago". A facilitator pointing at that lane is pointing at April 2020.
#
# The cause is a rename. EasyOCR moved `easy_ocr/` to `easyocr/` at c5472bc (2020-04-23) and
# `easyocr/model.py` to `easyocr/model/model.py` at c6f7ef0 (2020-11-08). The pre-rename chain
# stays alive under the old path (all 438 `easy_ocr/*` ops are in the ideal), while the
# post-rename chain never gets a creation op: `easyocr/easyocr.py::Reader` has nine distinct
# `pre` versions that no op in the store produces. Every op in a rootless segment is invalid,
# and ops that declare `requires` edges on those symbol-versions cascade out with them -- which
# is how easyocr/DBNet/ (added 2022-08-22, never renamed) and even `Dockerfile` end up
# invisible. This is an sgt defect, reported separately; it is not something a build script can
# work around.
#
# What a build script CAN do is start the history after the last rename, so every symbol in the
# boundary tree gets a creation op and roots correctly. A shallow clone does that with no
# rewriting and no special sgt flag: the boundary commit has no parents in the clone, so sgt
# mines it as genesis. `--shallow-since=2020-11-08` lands the boundary at d6fa6da (2020-12-01),
# after both renames, and keeps 275 commits and five years of real EasyOCR history -- DBNet,
# the CRAFT trainer, every recent release. `git log` in the demo repo stops in December 2020;
# say so rather than let a participant discover it.
SHALLOW_SINCE="${SHALLOW_SINCE:-2020-11-08}"
#
# The other lever, `sgt init --horizon <ref>`, is the documented way to bound mining and would
# in principle fix the same thing. It is not used here because it does not work at this scale:
# `sgt init --horizon c6f7ef0` on the full 619-commit clone printed nothing and had persisted
# zero ops after ten minutes, twice, and was killed rather than finishing. Unlike the default
# `sgt init`, which returns in 20 s and leaves a chunked backfill you can drive and watch, the
# horizon path gives you no progress and no partial state. Set FULL_HISTORY=1 to build the full
# clone anyway -- useful for investigating the defect above, useless as a demo.
FULL_HISTORY="${FULL_HISTORY:-0}"
#
# WHAT THIS COSTS (measured on an M-series laptop, 2026-09-01)
#
#   git clone --shallow-since       19 s, 80 MB of .git
#   sgt init --agent                20 s
#   mining 275 commits to genesis  ~10 min   <-- the whole cost
#   sgt log --rebuild (LLM labels) ~70 s
#   search index                   ~30 s
#   warming the four read paths    ~10 s
#
# Budget about 3 GB of disk: the checkout is 420 MB and the op store grows to a few hundred MB
# (the full-history build's `.sgt` reached 863 MB). Mining dominates the time and cannot be cut
# without cutting history. EasyOCR tracks 193 `easyocr/dict/*.txt` word lists, some 45 MB, and a
# file with no tree-sitter grammar is `opaque` tier -- one whole-file pseudo-symbol, re-hashed at
# every commit that touches it. Excluding them is not available to us: tier config is read from
# the mined commit's own tree (LAW-0, sgt/core/tiers.py:146), so a `.sgtignore` added today
# changes nothing about 2021.
#
# Mining is chunked against a per-contact deadline, so a single `sgt` call leaves the backfill
# mid-walk. This script drives it to completion in a loop and REFUSES to continue otherwise; a
# half-mined repo is the single most likely way this demo embarrasses us live, and it is silent
# from every angle -- `sgt log` still draws a map, `sgt find` still ranks -- so nobody would see
# it without a complete build to compare against.
#
# WHAT A REBUILD CANNOT PROMISE
#
# Feature LABELS come from an LLM and re-roll per build; the PARTITION itself moves whenever the
# entity graph changes. So feature IDS from a previous build are dead, and a dead one is worse
# than useless: `sgt revert <id>` falls back to matching it as an OP-ID PREFIX and reverts
# something unrelated while reporting success. The walkthrough doc therefore addresses features
# BY LABEL and never by id, and after any rebuild its labels must be re-read from
# `sgt log --tree` rather than trusted.
set -euo pipefail

SGT_SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
target="${1:-$HOME/repos/sgt-demo/EasyOCR}"
UPSTREAM="${UPSTREAM:-https://github.com/JaidedAI/EasyOCR.git}"

# `sgt` from PATH by default. Point SGT at another install to build against a frozen copy.
export SGT="${SGT:-sgt}"

say() { printf '  %s\n' "$*"; }

command -v "$SGT" >/dev/null 2>&1 || { echo "no sgt on PATH; set SGT=<path>" >&2; exit 1; }
say "sgt: $(command -v "$SGT")"

# The interpreter BEHIND the sgt on PATH, read off its shebang. Every python step below
# (driving the sync, the integrity gate, the search index) imports sgt, and it has to be the
# same sgt that did the mining -- a system `python3` almost never has it, and when it does it is
# a different install than the one being demoed.
SGT_PY="$(sed -n '1s/^#!//p' "$(command -v "$SGT")" 2>/dev/null || true)"
[ -x "${SGT_PY:-}" ] || { echo "could not find the python behind $SGT" >&2; exit 1; }
"$SGT_PY" -c 'import sgt' 2>/dev/null || { echo "$SGT_PY cannot import sgt" >&2; exit 1; }

# --- the LLM credential, checked BEFORE the ten-minute mine ------------------------------
#
# Feature names are an LLM call. Without a credential every one of them falls back to a joined
# list of symbol names -- `detect group_text_box…` instead of `Text Detection`. That is not a
# cosmetic loss for this demo, it is the demo: the first thing the facilitator points at is the
# feature map, and a terse map is indistinguishable from sgt having nothing to say. So resolve
# the key through sgt's OWN resolver (a shell export beats a stale `.env` line, sgt/config.py:65)
# and fail here rather than after the mine, when the cost of a rerun is ten minutes.
#
# The demo repo has no `.env` of its own, so the key comes from the shell -- or, failing that,
# from the source checkout's `.env`, which is where this machine keeps it.
if [ -z "${OPENAI_API_KEY:-}" ] && [ -f "$SGT_SOURCE/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$SGT_SOURCE/.env"
    set +a
    say "LLM key: from $SGT_SOURCE/.env"
else
    say "LLM key: from the shell environment"
fi
"$SGT_PY" - <<'PY' || exit 1
import sys
from sgt.config import resolve_api_key
if not resolve_api_key("."):
    sys.exit("no LLM credential: export OPENAI_API_KEY (or ANTHROPIC_AUTH_TOKEN for a Claude\n"
             "model) before building. Without one the feature map ships with terse symbol-list\n"
             "names, which is the one thing the demo cannot ship without.")
PY

# --- clone, pinned ----------------------------------------------------------------------
if [ -e "$target" ]; then
    [ "${FORCE:-0}" = 1 ] || { echo "$target exists; FORCE=1 to replace it" >&2; exit 1; }
    rm -rf "$target"
fi
mkdir -p "$(dirname "$target")"
if [ "$FULL_HISTORY" = 1 ]; then
    say "cloning $UPSTREAM (FULL history -- see the comment above; not a usable demo)"
    git clone --quiet "$UPSTREAM" "$target"
else
    say "cloning $UPSTREAM (shallow since $SHALLOW_SINCE, ~20 s)"
    git clone --quiet --shallow-since="$SHALLOW_SINCE" "$UPSTREAM" "$target"
fi
target="$(cd "$target" && pwd)"
cd "$target"

# A NAMED branch, not a detached HEAD. sgt keys its witness, backfill and sync cursors by ref
# (`refs/heads/master`), and several verbs resolve the branch through that record; a detached build
# mines under a bare-sha key and then disagrees with itself when a facilitator runs `git checkout
# master`. `master` and not `main` because that is EasyOCR's own default branch: `origin/HEAD`
# points at it, so a facilitator's `git checkout master` after a walkthrough lands back where the
# build left off. Renaming the ref AFTER a build is not free -- every per-ref cursor is keyed by the
# old name, so the first contact under a new name starts a fresh 10-minute backfill.
git rev-parse --verify --quiet "$PIN^{commit}" >/dev/null \
    || { echo "the pinned commit $PIN is not in this clone -- upstream moved, or"  >&2
         echo "SHALLOW_SINCE is later than the pin" >&2; exit 1; }
git checkout -q -B master "$PIN"
say "pinned at $(git rev-parse --short HEAD)  $(git log -1 --date=short --format='%ad  %s')"
say "$(git rev-list --count HEAD) commits, $(git ls-files | wc -l | tr -d ' ') tracked files"
if [ -f .git/shallow ]; then
    say "shallow boundary: $(git log -1 --date=short --format='%h %ad' "$(head -1 .git/shallow)")"
fi

# --- bind the kernel --------------------------------------------------------------------
#
# `--agent` because step 6 of the walkthrough shows sgt reaching a coding agent: it writes
# `.mcp.json`, the three skills into `.claude/skills/`, and `sgt.path` into
# `.vscode/settings.json` (which is also what makes step 7's workbench find this sgt rather than
# whatever is on the GUI's PATH). Doing it at init rather than by hand keeps the demo's agent
# wiring a property of the build.
say "sgt init --agent"
"$SGT" init --agent >/dev/null

# `.vscode/` is already in EasyOCR's own `.gitignore`; `.claude/` and `.mcp.json` are not, so
# without this they show as untracked forever -- and, worse, the walkthrough's reset step
# (`git clean -fd`) would DELETE the agent wiring after the first participant. Excluded locally
# rather than committed: committing them would put sgt's config into a diff against upstream,
# which is the one thing a demo of a third-party repo should not have.
cat >> .git/info/exclude <<'EOF'

# sgt agent + editor wiring written by `sgt init --agent`. Part of the demo build, not part of
# EasyOCR. Excluded so `git status` reads clean and `git clean -fd` (the walkthrough's reset)
# does not delete it. `.sgt-pristine.tar` is this build's own snapshot of `.sgt`, written at the
# end and read by the walkthrough's reset.
.claude/
.mcp.json
.sgt-pristine.tar
EOF

# --- drive the mine to completion -------------------------------------------------------
say "mining history to completion. About 10 minutes; it prints a line every ten contacts."
"$SGT_PY" - . <<'PY'
import sys, time
from sgt.core.lens import get, sync_status

t0 = time.time()
# 400 contacts is a ceiling against an infinite loop, not a budget: each contact advances at
# least one chunk, and the 275-commit shallow history took about 60 of them. If this ever
# exhausts the ceiling, the answer is to look at why a contact stopped advancing, not to raise it.
for i in range(400):
    get(sys.argv[1])
    st = sync_status(sys.argv[1])
    if st["complete"]:
        print(f"  mined to genesis after {i + 1} contact(s), {time.time() - t0:.0f}s")
        break
    if i % 10 == 0:
        print(f"  contact {i + 1}: {time.time() - t0:.0f}s", flush=True)
else:
    sys.exit("  history did not mine to completion in 400 contacts -- refusing to ship a "
             "half-mined demo")
PY

# --- name the features ------------------------------------------------------------------
#
# `--rebuild`, not `--refresh`: a full from-scratch recluster, so the graph is a function of the
# code alone rather than of the order this build happened to touch things. Runs with the key
# resolved above.
say "naming features (sgt log --rebuild). About 70 seconds."
rebuild_log="$target/.rebuild.log"
"$SGT" log --rebuild --no-color > "$rebuild_log" 2>&1 || true

# Whether the names are real is read off the label CACHE, not off the warning text and not by
# eyeballing the tree. Every entry is tagged `"source": "llm"` or `"source": "fallback"`
# (sgt/lens/label.py:327), which is the only signal that does not depend on a human noticing
# that "detect group_text_box" is a symbol list rather than a name. The stderr warning is
# reported too when present, because its wording names the cause.
if /usr/bin/grep -q "an LLM labeling call failed" "$rebuild_log" 2>/dev/null; then
    echo >&2
    /usr/bin/grep "an LLM labeling call failed" "$rebuild_log" >&2
    echo >&2
fi
"$SGT_PY" - "$target" <<'PY' || exit 1
import json, pathlib, sys
cache = pathlib.Path(sys.argv[1]) / ".sgt" / "local" / "label_cache.json"
body = json.loads(cache.read_text()) if cache.is_file() else {}
entries = body.get("data", body)
fell_back = [k for k, v in entries.items() if isinstance(v, dict) and v.get("source") == "fallback"]
llm = [k for k, v in entries.items() if isinstance(v, dict) and v.get("source") == "llm"]
print(f"  labels: {len(llm)} named by the LLM, {len(fell_back)} fell back")
if fell_back:
    sys.exit("  Some features carry terse symbol-list names instead of real ones. Re-run\n"
             "  `sgt log --rebuild` in the demo repo (it retries exactly the fallbacks) and\n"
             "  re-check; do not walk a participant through a map with placeholder names.")
PY
rm -f "$rebuild_log"

# --- the gate that decides whether the demo is usable -----------------------------------
#
# A degenerate graph is silent from every angle a builder would look at: `sgt log` still lists
# every lane, `sgt find` still ranks everything, and the hole only shows itself when someone asks
# a feature what it contains and is told "0 symbols in 0 files" -- on the shared screen.
#
# Advisory, not fatal, and that is a deliberate weakening of how the study bundles use this same
# script. `check_graph_integrity.py` also fails on "symbols in no frontier", which on a real
# third-party repo is not a build defect this script can fix (see the rename discussion above) --
# on the full clone it is 177 symbols, and cutting the history at 2020-11-08 brings it down to 119
# but not to zero. Making it fatal here would mean no EasyOCR demo at all. So the gate runs, its
# verdict is printed in full, and the operator decides. Read it: the numbers it prints are the ones
# the walkthrough's "Known rough edges" section has to match. Expect, at this pin, on 2026-09-01:
#
#   119 symbols an op touched are in no frontier and were never deleted
#   NOTE: 38 features carrying work but owning no symbol (finding 86, open, not a blocker)
#   409 symbols touched, 670 alive, 6 tombstoned, 670 placed in 67 leaf features
#   The graph is degenerate. Rebuild this repo; do not hand it to a participant.
#
# That last line is the gate's verdict on the 119, and on this repo it is the expected output rather
# than a reason to rebuild: a rebuild produces the same 119, because the cause is upstream's rename
# history and not this build. The 67 leaf features are also worth reading against the 29 the
# rendered tree below prints -- the render drops the 38 zero-symbol lanes, but every `--json`
# surface (and therefore the VS Code workbench) still reports 67.
say "checking the feature graph (advisory -- read the output)"
"$SGT_PY" "$SGT_SOURCE/scripts/check_graph_integrity.py" "$target" 2>&1 \
    | /usr/bin/grep -v '^    ' | sed 's/^/  /' || true

# --- the search index -------------------------------------------------------------------
#
# Embedded once here rather than on first use, because first use is step 3 of the walkthrough
# with a participant watching. Checked rather than assumed: an index with no embeddings still
# ANSWERS -- it falls back to word matching -- so nothing in a session would ever say out loud
# that half of what `sgt find` promises is missing.
say "building the search index"
"$SGT_PY" - "$target" <<'PY' || exit 1
import sys
from sgt.lens.search import build_index
if not build_index(sys.argv[1])["embedded"]:
    sys.exit("  the search index has no embeddings, so `sgt find` matches words rather than\n"
             "  meaning. Usually a missing or out-of-credit key. Fix and re-run; step 3 of the\n"
             "  walkthrough is the one that depends on this.")
print("  index embedded")
PY

# --- warm every read the walkthrough touches --------------------------------------------
#
# The first call on any of these paths pays for a cache every later call reads. Warming them here
# is the difference between a facilitator's opening command returning in a second and it returning
# in forty while they narrate an apology. Run twice and print the SECOND timing: the first is the
# fill, the second is what the participant will actually see.
say "warming the read paths (each run twice; the second timing is the live one)"
for cmd in "now" "log" "log --tree" "log --summary"; do
    # shellcheck disable=SC2086
    "$SGT" $cmd --no-color >/dev/null 2>&1 || true
    t="$("$SGT_PY" - "$SGT" $cmd <<'PY'
import subprocess, sys, time
argv = sys.argv[1:] + ["--no-color"]
t0 = time.time()
subprocess.run(argv, capture_output=True)
print(f"{time.time() - t0:.2f}")
PY
)"
    say "$(printf 'sgt %-14s %5ss' "$cmd" "$t")"
done

# --- pristine? -------------------------------------------------------------------------
#
# The demo is walked through repeatedly, and step 5 (the plan loop) deliberately edits code.
# `git status` clean at the END OF A BUILD is therefore the definition of "pristine", and the
# walkthrough's reset step restores exactly this state. Checked here so that the state the reset
# aims at is known to be reachable.
dirty="$(git status --porcelain | wc -l | tr -d ' ')"
[ "$dirty" = 0 ] || { echo "the build left $dirty dirty path(s); that is a bug in this script" >&2
                      git status --short >&2; exit 1; }

# --- the snapshot the walkthrough's reset actually needs --------------------------------
#
# `git checkout -- . && git clean -fd` puts the CODE back and nothing else, and step 5 of the
# walkthrough dirties far more than the code. Measured on a single rehearsal of it: four extra ops
# in `.sgt/ops` (the mined helper, the call site, the unpredicted import edit), a `plan_sessions.json`
# holding an active session, `turns.json`, two `local/hollow/*` predictions, `intent/prompts.json`
# from the `sgt intent record` hook that `init --agent` installs, and -- after `sgt plan abandon` --
# two open intents that then print on every `sgt log`. Every one of those is invisible to `git
# status`, and the first symptom of a half-cleaned repo is the staleness banner appearing on the
# facilitator's opening command in front of the next participant.
#
# So the build ends by tarring the whole of `.sgt` while it is known-good. The reset is then
# `git reset --hard <pin> && git clean -fd`, replace `.sgt` from this tar, and nothing else. About
# 170 MB and a few seconds -- cheap against the alternative, which is the 10-minute rebuild.
say "snapshotting .sgt for the walkthrough's reset (about 170 MB)"
rm -f .sgt-pristine.tar
tar -cf .sgt-pristine.tar .sgt
say "$(printf '.sgt-pristine.tar  %s' "$(du -h .sgt-pristine.tar | cut -f1)")"

echo
say "the feature tree the facilitator will open on:"
echo
"$SGT" log --tree --no-color | sed 's/^/    /'
echo
say "built: $target"
say "walkthrough: $SGT_SOURCE/docs/study/interview-demo-easyocr.md"
# The index built above is embedded, but `sgt find` embeds the QUERY at call time too, so the
# facilitator's shell needs the key as well -- and without it `find` does not error, it silently
# falls back to word matching and still prints a ranked list. The tell is uniform 0.50/0.25 scores.
say 'before a walkthrough: export OPENAI_API_KEY in the facilitator shell, or `sgt find` silently'
say '  degrades to word matching (uniform 0.50 scores). Pre-flight check 9 in the walkthrough.'
say "reset after a walkthrough (the doc's step 8 spells this out):"
say "  cd $target"
say "  git reset --hard $PIN && git clean -fd"
say "  rm -rf .sgt && tar -xf .sgt-pristine.tar"
