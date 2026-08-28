# Sketchpad demo runbook

Setup, checks, and the two ways this can break. `STORYBOARD.md` is the take itself.

The demo repo is `~/repos/sgt-demo/sketchpad`, a reimplementation of Sutherland's 1963 Sketchpad
built in nineteen saves. It is a good subject for sgt because the drawing is stored as conditions
rather than as lines, so an older version of the program reads the same file and draws a different
picture. Eighteen of the nineteen frontiers change what is on screen.

For the seedbank demo, read `RUNBOOK-seedbank.md`. It is a different repo and a different take.

---

## 1. Set the paths, once per terminal

```bash
export SGT_SRC=~/repos/semi-git
export DEMO=~/repos/sgt-demo/sketchpad
export SGT=$SGT_SRC/.venv/bin/sgt        # not the `sgt` on your PATH
```

The `sgt` on your PATH is probably not the one this demo needs. Check rather than assume:

```bash
$SGT_SRC/.venv/bin/python -c "import sgt.core.op as o; print(o._symbol_kind('a.ts::__import__::./b'))"
# must print: import
```

If it prints `nested`, that tree is on `main`. Check out `feat/live-render-timeline` there.

---

## 2. The one rule

**Do not run `sgt log --refresh`, `sgt log --rebuild`, or `sgt save` in the demo repo.**

`show the solving order` is an authored feature, built by hand so that reverting it removes one
save's work and nothing else. Every mining pass rewrites it. Measured:

| after | direct ops | what the revert removes | tsc errors |
|---|---|---|---|
| authoring it | 17 | 17 edits, 6 files | 0 |
| one `sgt log --refresh` | 35 | 195 edits, 10 files | 50 |
| one `sgt save` | 38 | 208 edits, 10 files | 49 |

Nothing warns you. Findings 79 and 80 in `docs/study/sgt-findings.md` have the detail.

If it does get rewritten, section 6 rebuilds it.

---

## 3. Window layout

| where | what |
|---|---|
| Browser tab 1 | the app, `http://localhost:5174/`, up the whole take |
| Browser tab 2 | the timeline page, `file:///tmp/sketchpad-timeline.html` |
| Terminal A | `npm run dev` in `$DEMO`, never on camera |
| Terminal B | the sgt commands you type on camera, beats 2 and 5 |
| Terminal C | the scratch clone, for beat 4 only |

Start the app:

```bash
cd $DEMO && npm run dev      # serves on 5174
```

---

## 4. Preflight, immediately before recording

Run these five and read every answer. They take about a minute together.

```bash
cd $DEMO

# 1. the tree is clean, so `sgt undo` has something exact to return to
git status --short                       # must be empty

# 2. the program compiles
rm -f tsconfig.tsbuildinfo && npx tsc --noEmit && echo "0 errors"

# 3. the map is the one the storyboard points at
$SGT log --map                           # 11 features, 19 saves, no ▸ collapsed rows

# 4. the authored feature is still exact
$SGT feature select "show the solving order" | head -1
# must say: 17 direct op(s)

# 5. the revert still lands, on a throwaway copy
bash $SGT_SRC/scripts/demo/check-revert.sh
```

The preflight clones, reverts, compiles, undoes, and compares, and prints nine lines. You want
nine passed. It also refuses to run against the wrong `sgt` build: with the wrong one the revert
previews correctly and then refuses to apply, naming four files it never touches (finding 84).

If check 4 says anything but 17, stop and go to section 6.

---

## 5. Beat 4 runs on a clone, never on the demo repo

The revert is reversible and `sgt undo` restores the tree byte for byte, which is verified. It is
still not what you want to be doing live on the repo the app is serving from. Make the clone
before you start recording:

```bash
rm -rf /tmp/sketchpad-live && git clone -q $DEMO /tmp/sketchpad-live
cp -r $DEMO/.sgt /tmp/sketchpad-live/.sgt
ln -s $DEMO/node_modules /tmp/sketchpad-live/node_modules
cd /tmp/sketchpad-live && npx vite --port 5175
```

Do not run `sgt advanced resync` in that clone. It is finding 73's documented workaround, it is not
needed here, and it re-derives the op set: on this repo it moved the authored feature from
seventeen direct ops to fifteen, which is the drift section 2 is about.

Beat 4 then types in `/tmp/sketchpad-live` and shows `http://localhost:5175/`.

The two ports look identical on camera, which is the point: the audience sees one app.

---

## 6. Rebuilding the authored feature

Needed only if `sgt feature select "show the solving order"` stops saying 17.

There is no verb that creates an authored feature from a selection, so this goes the long way
round. `<save>` is the commit that introduced the overlay, `3acfbe7`, and `<prev>` is the one
before it, `cfbd0fb`.

```bash
cd $DEMO

# the ops that save introduced, which is the whole membership
comm -13 <(git log -1 --format=%B <prev> | grep -i "^Sgt-Op:" | awk '{print $2}' | sort) \
         <(git log -1 --format=%B <save> | grep -i "^Sgt-Op:" | awk '{print $2}' | sort) > /tmp/newops.txt
wc -l < /tmp/newops.txt          # 17

# whatever else drifted into the lane
$SGT log --map --json | python3 -c "
import json,sys
d=json.load(sys.stdin)
ops=set()
for c in d['cells']:
    if c['feature_id'].endswith('0ffcd7814db6ea54f6c1672e2ec81e202848a66de579a61829a423aa3290546a'):
        ops.update(c['op_ids'])
want=set(l.strip() for l in open('/tmp/newops.txt') if l.strip())
open('/tmp/intruders.txt','w').write('\n'.join(sorted(ops-want)))
print(len(ops-want), 'to move out')"

$SGT feature regroup move $(tr '\n' ' ' < /tmp/intruders.txt) --to 002b21d3 --json >/dev/null
$SGT feature regroup move $(tr '\n' ' ' < /tmp/newops.txt) --to 0ffcd781 --json >/dev/null
$SGT feature select "show the solving order" | head -1     # 17 direct op(s)
```

If the lane `0ffcd781` no longer exists at all, mint one with
`$SGT feature regroup split 002b21d3 --apply`, take the new id from the output, move the ops into
it, and `$SGT feature rename <new id> "show the solving order"`.

A copy of a known-good store lives in `/tmp/sgt-golden` for as long as that machine stays up.
`rm -rf .sgt && cp -r /tmp/sgt-golden .sgt` is faster than any of the above when it is there.

---

## 7. Rebuilding the timeline page

Only needed after a new save. Takes about four minutes.

```bash
cd $SGT_SRC
SGT=$SGT_SRC/.venv/bin/sgt bash scripts/demo/render-frontiers.sh $DEMO /tmp/sketchpad-frontiers

TIMELINE_TITLE="sketchpad, built one feature at a time" \
TIMELINE_SUB="every frame is that commit folded onto disk, served, and photographed. drag to watch the drawing arrive." \
bash scripts/demo/build-timeline-page.sh /tmp/sketchpad-frontiers /tmp/sketchpad-timeline.html $DEMO
```

The sweep prints a table of which frontiers moved the picture. Eighteen of nineteen should say
`changed`. The one that says `IDENTICAL` is the constraint-type-table save, and it is meant to.

`render-frontiers.sh` reads the store and writes only to its output directory, so it is safe to
run against the demo repo. It does not refresh anything.

---

## 8. When the page does not change

Symptoms and causes, in the order they actually happen.

**The app shows a stale picture after a revert.** Vite has cached the old module. Hard reload the
tab. If that fails, restart `vite` in that clone.

**`sgt advanced fold --at N` says "fork-freedom violated".** The store has drifted. `fsck` and
`forks` will both say the repo is healthy and both are wrong; `resync` will say "unchanged" and do
nothing. The working repair is `rm -rf .sgt && sgt init`, which is safe because git holds all the
content. It also destroys every authored feature, so section 6 has to run afterwards. Finding 78.

**`sgt revert "<name>"` says the feature is not found.** Something rebuilt. `sgt log --map` will
show a different set of names. Restore `/tmp/sgt-golden` or go to section 6.

**The revert preview looks right but `tsc` fails afterwards.** The authored feature has drifted
even though its name still resolves. Check `sgt feature select` first; it is the cheap test.

---

## 9. What has not been checked from a terminal

The VS Code workbench renders in a webview and cannot be screenshotted from here. The headless
smoke test over the real render path passes:

```bash
cd $SGT_SRC/editor/vscode && node dev/smoke.js      # SMOKE OK
```

That covers the graph render path on real data. It is not a visual pass. If the workbench is going
on camera, open it in VS Code against `$DEMO` and look at it yourself first.
