# Closing demo: sgt on EasyOCR

A facilitator's script for the **"Your own repository"** step that closes a session (protocol v2
§7, `web/src/participant/steps/Interview.tsx`). The participant is meant to open a repo of their
own; most arrive without one, so this is the prepared fallback: the facilitator drives a
third-party codebase on the shared screen and the participant reacts to it.

**Budget 12–15 minutes.** Steps 1–7 below are 2–3 minutes each; step 8 happens after the
participant has left.

- **Repo:** `~/repos/sgt-demo/EasyOCR`, built by `scripts/demo/build-easyocr-demo.sh`.
- **sgt:** whatever the build used. On this machine `/Users/r4yen/repos/semi-git/.venv/bin/sgt`,
  and `.vscode/settings.json` + `.mcp.json` in the demo repo already point at it. Every command
  below is written as plain `sgt`; put that venv on your `PATH` first, or type the full path.
- **Every output snippet in this document was run and observed** on the built repo at pin
  `363afb1`, 2026-09-01, including a full end-to-end rehearsal of step 5. Two exceptions, both
  flagged where they appear: step 6's live `claude` session, and step 7's on-screen description of
  the workbench. Numbers move if the repo is rebuilt — see "After a rebuild" at the end.

---

## Read this first, aloud, before any command (about 60 seconds)

> EasyOCR is an open-source OCR library: you hand it a picture that has words in it and it hands
> you back the words. It does that in two stages — **detection** finds the boxes on the page that
> contain text, **recognition** reads what is inside each box — and it ships models for about
> eighty languages. Neither of us wrote any of it. That is the point: everything sgt is about to
> say about this codebase, it worked out from the history.

Do not go further than that. No architecture, no model names. The participant's attention should
be on the graph, not on OCR.

One more sentence you owe them, before they find it themselves:

> Heads up on one thing: `git log` in this checkout stops in December 2020. I cloned it shallow on
> purpose, so the history you'll see runs from there to now and no earlier.

(The reason is in the build script: EasyOCR renamed two directories in 2020, and sgt cannot root
the post-rename symbol chains across those renames. Cutting the history after the last rename is
what makes the graph answer questions at all. You do not need to explain that to a participant,
but if they ask why, that is the honest answer.)

---

## Pre-flight, before the participant is in the room

Run all of it. Each check has cost someone a live session at some point.

```bash
cd ~/repos/sgt-demo/EasyOCR
```

| # | Check | Command | Expect |
|---|---|---|---|
| 1 | Repo is there, on the pin | `git log --oneline -1 && git rev-parse --abbrev-ref HEAD` | `363afb1 Update README.md` and `master` |
| 2 | Tree is clean | `git status --porcelain` | no output at all |
| 3 | No staleness banner | `sgt log --no-color 2>&1 \| grep -c 'sgt log --refresh'` | `0`. Anything else means the repo has drifted |
| 4 | Mining finished | `sgt-python -c "from sgt.core.lens import sync_status; print(sync_status('.'))"` | `{'complete': True, 'reached_genesis': True, 'history_rewritten': False}` |
| 5 | Labels are real, not placeholders | `sgt log --tree` | names like `MobileNetV3 Backbone`, `DBNet Segmentation Loss`. If you see `detect group_text_box…`, the LLM labelling fell back — rebuild |
| 6 | No plan or intent residue | `sgt plan status && sgt intent open` | `(no active plan sessions)` and `no open intents` |
| 7 | Reads are warm | see below | every command under 1.5 s |
| 8 | Terminal is wide enough | `tput cols` | **120 or more.** At 100 the feature names truncate to `Image Prepro…` and the map is unreadable |
| 9 | `sgt find` has its key | `sgt find "the bit that detects text boxes" \| head -3` | top hit `Text Box Processing` at **0.61**. If every score is exactly `0.50`, see below |

`sgt-python` in check 4 means the interpreter behind the `sgt` you are using — on this machine
`/Users/r4yen/repos/semi-git/.venv/bin/python3`. A system `python3` will not have sgt importable.

Check 3 is the one that matters most. `sgt log` prints its warnings on **stderr**, which is why the
command merges them with `2>&1`. A non-zero count means this line is printing:

```
 (173 saved edits and unsaved edits not shown yet — `sgt log --refresh`)
```

That means the repo has drifted since the build — almost always a leftover from a previous
walkthrough's step 5. Do the reset in step 8, then re-check. Do not just run `sgt log --refresh`
and move on: refresh clears the banner without undoing the leftover work.

Two things about that line, because you will meet it again during step 5 and it is misleading:

- **It fires on any dirty file.** Editing one line of `README.md` is enough. So it appears the
  moment step 5b's edits land, which is correct and harmless.
- **The "173 saved edits" half is not real.** 173 of this repo's 2,066 live edits are in no
  feature — permanently, for the reasons in "Known rough edges" — and the banner reports that count
  whenever it fires, as though a refresh would pick them up. It will not. Only the "unsaved edits"
  half of the sentence ever changes.

Check 7, warm timings measured on this build:

```
  sgt now             0.43s
  sgt log             0.47s
  sgt log --tree      0.49s
  sgt log --summary   1.12s
  sgt find "<query>"  1.4–3.0s   (one embedding call per query — needs network)
```

Check 9 is the sharp one, and it is easy to miss because it fails **silently**. `sgt find` embeds
your query at call time, so it needs `OPENAI_API_KEY` in the environment — the demo repo has no
`.env` of its own, and the build read the key out of the source checkout's. Without it `sgt find`
does not error: it falls back to word matching and still prints a ranked list. Side by side on the
same query:

```
with the key                                  without it
  0.61  feature  Text Box Processing            0.50  feature  Text Detection Inference · craft
  0.59  symbol   easyocr/utils.py::group_…      0.50  feature  Text Box Extraction · craft
  0.58  feature  Text Box Extraction · craft    0.50  feature  CRAFT Box Enlargement
  0.55  save     fix bug when text box …        0.50  feature  Text Box Processing
  0.53  symbol   easyocr/detection.py::get_…    0.25  symbol   trainer/craft/utils/inference_…
```

**The tell is the scores: uniform `0.50` and `0.25` means the fallback is running.** So does an
answer with no `symbol` rows near the top.

The demo repo now carries its own `.env` with just the key in it, mode 600, and `sgt` reads a `.env`
from the repository root (`sgt/config.py`), so this works with nothing exported — which matters
because a VS Code launched from Finder has no key in its environment and the workbench's search box
would fall back silently. EasyOCR's own `.gitignore` already lists `.env`, so it cannot be
committed; `git status` stays clean. If the file is missing, either recreate it or export the key by
hand:

```bash
grep '^OPENAI_API_KEY=' /Users/r4yen/repos/semi-git/.env > ~/repos/sgt-demo/EasyOCR/.env
chmod 600 ~/repos/sgt-demo/EasyOCR/.env
# or, for one shell only:
set -a; . /Users/r4yen/repos/semi-git/.env; set +a
```

`sgt find` is also the only command that talks to the network during the demo. Nothing is cached,
so a wifi failure happens live. Run each of step 3's queries once before the participant arrives,
and have a fallback ready: `sgt log --tree` answers "where is X" less precisely but offline.

---

## 1. Orient (about 2 minutes)

**Say:** "Two commands. The first asks where things stand, the second draws the whole history."

```bash
sgt now
```

```
 ⋔ 2 open forks — divergent edits to one symbol:
     "trainer/craft/config/custom_data_release_test_0\r.yaml\r\r"  →  sgt resolve "…"
     "trainer/craft/config/custom_data_release_test_3\r.yaml\r\r"  →  sgt resolve "…"
recently done
    363afb18  Update README.md
    c4f3cd72  update v.1.7.2
    3d3852d8  Merge pull request #1283 from Yepness/master
    86094d69  Fix: import get_display
    c999505e  v1.7.1
→ next      resolve fork on "trainer/craft/config/custom_data_release_test_0\r.yaml\r\r"
```

**Get ahead of the fork lines.** They are the first thing on screen and they look like an error.
They are not:

> Those two are real. Somewhere in EasyOCR's history a contributor committed two config files
> whose *filenames* contain carriage returns — a Windows line-ending accident — and later deleted
> them. sgt sees two competing versions of each and is asking a human which one wins. Nobody
> answered, so it keeps asking. It is a fair thing for it to ask and it is the only thing this
> repo has that needs a person.

Then move on. Do not run `sgt resolve`.

```bash
sgt log
```

```
 29 features  ·  275 saves  ·  9 subsystems

   FEATURE                        ID        c0 ─────────────────────────────────── c274  EDITS  LATEST
 ──────────────────────────────────────────────────────────────────────────────────────────────────────
   ▾ EasyOCR  ·  29 features
   ├─ ● Image Preprocessing       05dc0201   ▄                                              22  @0 Update French dict u…
   ├─ ▾ DBNet Detection Archite…  ·  7 features
   │  ├─ ● Deformable Convoluti…  00886bb2                              ▄                   19  @0 add dbnet
   │  ├─ ● DBNet Segmentation L…  0477f1d7                              ▄                   30  @0 add dbnet
   │  ├─ ● MobileNetV3 Backbone   081c6cee                              ▄                   26  @0 add dbnet
   …
 ──────────────────────────────────────────────────────────────────────────────────────────────────────
   ↕ work across several features
   ◆ DBNet Addition
   ◆ Training Code                                          █                        █
   ◆ Language Model Updates            █          ██     █
   ◆ Batched OCR Processing                          █
     … and 11 more
```

Three things to point at, in this order:

1. **The lane list on the left.** "sgt read 275 commits and grouped them into 29 pieces of work.
   Nobody labelled these — the names are generated from the code in each group."
2. **The columns.** "Left is 2020, right is now. So `MobileNetV3 Backbone` arrived in one burst
   in the middle and has not been touched since; `Image Preprocessing` is right at the start."
3. **The `◆` rows at the bottom.** "Those are single pieces of work that landed in several
   features at once. `Language Model Updates` is one — adding a language touches the model, the
   character lists and the reader."

**Good question to ask here:** "You know OCR. Looking at those names, is that roughly how you'd
have carved this codebase up?" That is the whole point of using a repo neither of you wrote.

---

## 2. Read a feature (about 2.5 minutes)

> **Address features by their label, in quotes. Never by the short id.**
> Ids are regenerated on every build. A dead id does not fail — `sgt revert <id>` falls back to
> matching it as an *op-id prefix* and reverts something unrelated while reporting success. The
> labels below were read off this build's `sgt log --tree`; re-read them after any rebuild.

Start with detection, because it is the smaller half of the model:

```bash
sgt show "Text Detection Inference · detection.py"
```

```
feature 093a1e97  "Text Detection Inference · detection.py"
  44 edits · 3 symbols in 1 file · last touched 707d ago
  symbols      easyocr/detection.py::get_detector, easyocr/detection.py::get_textbox,
               easyocr/detection.py::test_net
  saves        d6fa6da  Update French dict using https://salsa.debian.org/gpernot/wfrench
               78be56f  Enable batched tensor feeding to CRAFT torch model
               b6c8f02  Fix detection speed issue
               803b907  add dbnet
               c4f3cd7  update v.1.7.2
  reverting this removes 39 edits
```

**Say:** "Three functions in one file, and that is the detection entry point — `get_textbox` is
literally 'give me the boxes'. And look at the five commits underneath: someone made it faster,
someone made it take batches, someone bolted a second detector onto it. That list is the *reason*
this code looks the way it does, and it took one command."

Note the `·  detection.py` suffix on the label. sgt found two different pieces of work that both
deserved the name "Text Detection Inference" and disambiguated them by file — the other one is
`Text Detection Inference · craft`, the older CRAFT-based path. Worth pointing at: "it noticed
there were two and told us which is which."

Then the recognition side:

```bash
sgt show "Text Recognition Preprocessing"
```

```
feature 0230e633  "Text Recognition Preprocessing"
  104 edits · 11 symbols in 3 files · last touched 1471d ago
  symbols      easyocr/detection.py::get_detector, easyocr/easyocr.py::Reader,
               easyocr/easyocr.py::Reader.__init__, easyocr/easyocr.py::Reader.recognize,
               easyocr/recognition.py::AlignCollate, easyocr/recognition.py::ListDataset,
               easyocr/recognition.py::NormalizePAD, easyocr/recognition.py::custom_mean,
               easyocr/recognition.py::get_recognizer, easyocr/recognition.py::get_text,
               easyocr/recognition.py::recognizer_predict
  saves        d6fa6da  Update French dict using https://salsa.debian.org/gpernot/wfrench
               1434130  v1.2.5
               cdef0b4  fix confident score2
               59a413e  Make sure GPU behavior still calls flatten_parameters
               803b907  add dbnet
  reverting this removes 88 edits
```

**Say:** "That is the other half — `Reader`, which is the class you actually call, plus
`get_text`, which runs the recogniser. `fix confident score2` and the GPU one are the kind of
commit you'd never find by reading the code."

**Be honest about the leak.** `easyocr/detection.py::get_detector` is in this feature and it does
not belong here — it is detection, and it is *also* in the detection feature above. Say so: "the
grouping is not perfect; that first one is on the wrong side of the line." Participants notice,
and conceding it costs nothing.

Then the checkpoint view:

```bash
sgt log --focus "Text Detection Inference · detection.py"
```

The map redraws with every other lane dimmed, and a table appears below it:

```
 ● Text Detection Inference · detection.py  ·  093a1e97  ·  4 checkpoints  ·  3 reverted

      CHECKPOINT               WHEN     EDITS  STATE
 ───────────────────────────────────────────────────────
  @0  Text Detection Setup     c8          39
  @1  Batched CRAFT Inference  c69–c83      3  reverted
  @2  DBNet Support            c185         1  reverted
  @3  Detector Version Update  c273         1  reverted
```

**Say:** "sgt cut that feature's 44 edits into four chapters and named each one. `Batched CRAFT
Inference` is the batching commit we just saw."

**`reverted` does not mean anyone reverted anything.** It means "none of this checkpoint's edits
are still live" — later work overwrote them. `sgt show 093a1e97@1` spells it out:
`nothing here is currently live — a revert would be a no-op`. Say the plain-English version
("that work has since been written over") rather than reading the word off the screen, or you will
spend two minutes explaining a display choice.

---

## 3. Search by description (about 2 minutes)

**Say:** "Now the part you can't do with git. I'm going to describe what I'm looking for in
English, without knowing any names in this codebase."

```bash
sgt find "the bit that detects text boxes"
```

```
  0.61  feature  Text Box Processing
        f-6825ec5251d8  This code transforms images and groups detected text boxes using…
  0.59  symbol   easyocr/utils.py::group_text_box
        in Text Box Processing
  0.58  feature  Text Box Extraction · craft
        f-0998b66c3f1a  These utilities convert CRAFT score maps into detected text boxes…
  0.55  save     fix bug when text box ratio is too slim
        c2105721  save c2105721
  0.53  symbol   easyocr/detection.py::get_textbox
        in Text Detection Inference · detection.py
```

Strong hit. Point at the mix: "it came back with a feature, a specific function, and a bug fix,
all for one English sentence."

```bash
sgt find "how the recognition model reads a cropped word"
```

```
  0.58  symbol   easyocr/recognition.py::recognizer_predict
        in Text Recognition Preprocessing
  0.57  symbol   easyocr/recognition.py::get_text
        in Text Recognition Preprocessing
  0.56  symbol   easyocr/recognition.py::get_recognizer
        in Multilingual OCR Recognition
  0.55  feature  Text Recognition Preprocessing
```

Strong. The top three are exactly right and the phrasing shares no words with the code.

```bash
sgt find "the training loop"
```

Top hit `trainer/train.py::train` at 0.61. Strong, and a good contrast: a short query works too.

**Then run one that fails, on purpose.** Do not skip this — a demo where everything works reads as
a sales pitch, and this failure is easy to explain.

```bash
sgt find "where the model weights get downloaded"
```

```
  0.50  symbol   trainer/saved_models/folder.txt
        in OCR Model Assets
  0.47  symbol   trainer/craft/model/vgg16_bn.py::init_weights
        in VGG16 Backbone
  0.44  save     fix download links
        f411163a  save f411163a
```

**Say:** "That is wrong, and interestingly wrong. The real answer is a function called
`download_and_unzip`, and it is not in the index at all — sgt only indexed 254 of this repo's 670
symbols, because a chunk of them never got attached to any feature. So the search is only as good
as the graph underneath it, and on somebody else's repo that graph has holes."

Note this one's top score is 0.50 with a healthy key — a weak semantic match, not the fallback.
The pre-flight tell is *every* score being exactly 0.50 or 0.25, not one of them.

(`where languages get loaded` fails the same way and is less interesting: it returns a wall of
"Add Pashto language support" commit subjects instead of the code that reads the character lists.
Use the weights one.)

`sgt find`'s `next:` hint offers `sgt show f-6825ec5251d8` — an id. Type the label instead.

---

## 4. Reach and consequence (about 2 minutes)

> **Never type `--yes` during a demo.** Without it, `sgt revert` prints a preview and writes
> nothing. Verified on this repo: `git status` and `.sgt/local/ideal.json` are byte-identical
> before and after.

Small one first:

```bash
sgt revert "Text Detection Inference · detection.py"
```

```
 ▸ rewind  Text Detection Inference · detection.py  093a1e97  ░░░░░░░░░░░░  39→0 edits
      sgt revert 093a1e97

   ▸ [0███]  @0 Text Detection Setup                     ░░░░░░░░░░░░  · removed
   ✗ (1░░·)  @1 Batched CRAFT Inference                  ░░░  · removed
   ✗ [2░░░]  @2 DBNet Support                            ░  · removed
   ✗ [3░░░]  @3 Detector Version Update                  ░  · removed

 · 66 other features unchanged
 removes 39 edits · 1 file: easyocr/detection.py

  not applied — this was the preview. re-run with --yes to apply.
```

**Say:** "Contained. One file, 39 edits, nothing else in the repo moves."

Now the one that is not contained:

```bash
sgt revert "DBNet Segmentation Loss"
```

```
 ▸ rewind  DBNet Segmentation Loss  0477f1d7  ░░░░░░░░░░░░  30→0 edits

   ▸ [0███]  @0 add dbnet                                ░░░░░░░░░░░░  · removed

 also affected
   ● Dice Losses                   █████████░░░  loses 6 edits, re-draft
   ● DBNet Decoder Heads           ███████░░░░░  loses 10 edits, re-draft
   ● Progressive Scale Loss        ██████████░░  loses 2 edits, re-draft
   ● Masked L1 Loss                ███████░░░░  loses 4 edits, re-draft
   ◈ DBNet Decoders                ████████████  gains 1 edit
   ● Balanced Cross-Entropy        ███████░░  loses 2 edits, re-draft

 · 60 other features unchanged
 removes 54 edits across 88 symbols · 7 files: easyocr/DBNet/decoders/__init__.py, …

  not applied — this was the preview. re-run with --yes to apply.
```

**Say:** "Same question, different answer. Pulling the segmentation loss out drags six other
pieces of work with it — five of them lose edits, and one of them *gains* an edit: take the newest
version of a decoder away and an older version becomes live again, and that older version belongs
to a different piece of work. That last one is the part I would never have worked out by hand."

`sgt show` gives the same thing as a number if you prefer to lead with it:
`reverting this removes 54 edits, 24 of them work built on top`.

**Two things on this screen will not match what you showed in step 1, and a participant may catch
them.** Get in front of both:

- **"66 other features"** and **"60 other features"**, when step 1 said 29. The preview counts
  sgt's internal 67 leaf features; the map and tree only draw the 29 that own at least one symbol.
  Say: "there are 67 groups underneath; 38 of them are bookkeeping with no code of their own, so
  the map hides them."
- **`Dice Losses`, `Masked L1 Loss`, `Balanced Cross-Entropy`** and the rest of the "also
  affected" list are exactly those 38. They will not be in `sgt log --tree`, and
  `sgt show "Dice Losses"` will answer but show 0 symbols. Do not go looking for them on screen.

---

## 5. The agentic loop (about 3 minutes)

**Say:** "Last piece. So far this has been a person reading history. The other half is an agent
writing it. Before it starts, it states what it intends to do; then the graph checks the work
against the statement."

This is the flow the `sgt-plan` skill defines. An agent runs it through MCP
(`sgt_plan_intake` → work → `sgt_checkpoint` → `sgt_plan_done`). **Drive it from the CLI
yourself** — it is the same state underneath, it is deterministic, and it fits in three minutes,
where launching a live agent session does not. Say which you are doing: "these are the same
operations a coding agent makes; I'm typing them so we can watch each one."

### 5a. State the intent

```bash
sgt plan intake "Add a min_confidence filter to Reader.readtext so callers can drop low-confidence OCR results.
Step 1: easyocr/utils.py::filter_by_confidence -- a helper that drops (bbox, text, confidence) items below a threshold.
Step 2: easyocr/easyocr.py::Reader.readtext -- accept min_confidence and apply the helper to the standard-format result."
```

```
✓ intake: session db3c1af1207a44d0b3fa4afbd1b8ed66 — 2 step(s)
    Implement confidence filtering helper  [f-6825ec5251d875f942a1a1beeaf40e82e9798e1a25f157b20467796d241d662a]
    Add min-confidence filtering to readtext  [f-01b39f6d058379545977699a8f14e8d890d56fbf43969d7116e9a74972ba801d]
```

**Say:** "It named the two steps, and the ids in brackets are its guesses about which existing
feature each step will land in — before a line of code exists. The first guess is `Text Box
Processing`, which is right."

(The second guess, `f-01b39f6d05`, is one of the 38 zero-symbol features — `OCR Text Extraction`.
Do not look it up on screen.)

The session id is different every run. Keep the terminal scrollback; you need it in 5c.

### 5b. Do the work

Two edits, prepared below so you can paste them. Nothing runs a model, nothing downloads
anything.

Append to `easyocr/utils.py`:

```python
def filter_by_confidence(results, min_confidence):
    """Drop results whose recognition confidence is below min_confidence.

    Each "result" is of the form (box coords, text, confidence). Results
    carrying no confidence (paragraph mode merges it away) are kept.
    """
    return [result for result in results
            if len(result) < 3 or result[2] >= min_confidence]
```

In `easyocr/easyocr.py`, three small changes: add `filter_by_confidence` to the existing
`from .utils import …` list; change `readtext`'s signature line
`output_format='standard'):` to `output_format='standard', min_confidence = 0.):`; and just before
its `return result`, add

```python
        if min_confidence > 0 and output_format == 'standard' and detail == 1:
            result = filter_by_confidence(result, min_confidence)
```

Then:

```bash
sgt now
```

```
working on  Implement confidence filtering helper
unsaved     3 edit(s), 2 new in 1 feature
            step 1 of 2
```

**Say:** "It picked the edits up off the working tree without being told, and it is tracking the
plan: step 1 of 2."

### 5c. Check the work against the plan

```bash
sgt plan status --json --full
```

That is the CLI form of an agent's `sgt_checkpoint` preview. It is JSON, so read it out rather
than pointing at it. The parts that matter:

```
step Implement confidence filtering helper
   predicted_footprint: ["easyocr/utils.py::filter_by_confidence"]
   status: pending
step Add min-confidence filtering to readtext
   predicted_footprint: ["easyocr/easyocr.py::Reader.readtext"]
   status: pending

match 0  hollow ['44238e677845']  ops ['980e93828cc7', 'e6386429499d']
match 1  hollow ['7f4ddbb41a8f']  ops ['e6386429499d']
drift ops: ['5e16c5e70cd4']
```

The three ops, by footprint:

| op | kind | footprint |
|---|---|---|
| `980e9382` | `add` | `easyocr/utils.py::__anchor__::filter_by_confidence` |
| `e6386429` | `extend` | `easyocr/easyocr.py::Reader`, `Reader.readtext`, `easyocr/utils.py::filter_by_confidence` |
| `5e16c5e7` | `rework` | `easyocr/easyocr.py::__import__::.utils` |

**Say:** "Two of the three edits map onto the two steps I declared. The third — `drift` — is the
import line I had to touch to wire the helper up. I never said I would, so sgt is holding it out
and telling me about it. That is the useful direction for this to fail in: it is not asking me to
fix anything, it is telling me what I did that I hadn't said I would."

Then commit and confirm the two matches:

```bash
git commit -am "Add a min_confidence filter to Reader.readtext"
sgt save --resolve-plan --confirm-hollow 44238e6778… --confirm-op 980e93828c…
sgt save --resolve-plan --confirm-hollow 7f4ddbb41a… --confirm-op e638642949…
```

Use the **full 64-character** `hollow_id` and op id, copied out of the
`sgt plan status --json --full` output — that is what was rehearsed. Shortened prefixes were not
tested here, so do not improvise them live.

```
✓ confirmed 1 hollow(s) matched to 1 op(s) in session db3c1af1207a44d0b3fa4afbd1b8ed66
✓ confirmed 1 hollow(s) matched to 1 op(s) in session db3c1af1207a44d0b3fa4afbd1b8ed66
```

```bash
sgt plan status
```

```
(no active plan sessions)
```

**Say:** "Both steps matched, so the plan closed itself. If I'd built something I hadn't declared,
it would still be sitting there open, which is the point."

**Then stop.** Do not go on to `sgt show "easyocr/utils.py::filter_by_confidence"` — see the rough
edges section; on this repo the new symbol never enters a feature and that command fails.

**After the participant leaves, do step 8.** Step 5 is the only part of this walkthrough that
writes anything, and it writes more than `git status` can see.

---

## 6. sgt from inside Claude Code (about 1.5 minutes)

**This is real and already wired up in the demo repo — nothing to configure.** `sgt init --agent`
(run by the build) wrote three files:

```
.mcp.json                  the sgt MCP server, 18 tools
.claude/settings.json      {"enabledMcpjsonServers": ["sgt"]}  — so Claude Code does not prompt
.claude/skills/            sgt-agent, sgt-plan, sgt-workflow
```

`.mcp.json` is exactly:

```json
{
  "mcpServers": {
    "sgt": { "command": "/Users/r4yen/repos/semi-git/.venv/bin/sgt", "args": ["mcp"] }
  }
}
```

Verified by hand: starting `sgt mcp` in the demo repo initialises as `semi-git 0.1.0` and lists 18
tools — `sgt_now`, `sgt_show`, `sgt_find`, `sgt_log`, `sgt_status`, `sgt_diff`, `sgt_recall`,
`sgt_save`, `sgt_revert`, `sgt_restore`, `sgt_init`, `sgt_drift`, `sgt_checkpoint`,
`sgt_plan_intake`, `sgt_plan_adopt`, `sgt_plan_done`, `sgt_advanced_fsck`,
`sgt_advanced_oracle_run`. `sgt_show` called over MCP on `"Text Detection Inference ·
detection.py"` returns the same feature as step 2, as JSON.

**The one thing to demo.** Run `claude` in `~/repos/sgt-demo/EasyOCR` and ask:

> Using sgt, what is the detection side of this codebase and what would it cost to remove it?

The agent has no reason to grep: one `sgt_show` call answers both halves. Verified by hand over the
MCP wire, that call returns `"symbol_count": 3`, the three `easyocr/detection.py` functions, the
five save subjects, and `"removes": 39, "dependents": 0`.

**Not rehearsed:** the live `claude` session itself. The MCP server, the tool list and the
`sgt_show` result above were all verified against the demo repo; whether the agent reaches for
`sgt_show` rather than grep on any given run was not. If you want this step to be certain, run the
prompt once during pre-flight — or say the sentence below over the `sgt show` output from step 2
instead and skip launching an agent.

**Say:** "The same reads I've been typing are tools an agent can call. It doesn't have to read the
codebase to find out what removing something costs — it asks."

**Be straight about two limits if asked:**

- `.claude/settings.local.json` also installs two hooks — `sgt intent record` on every prompt you
  submit and `sgt intent activity` on every file edit — so a live Claude Code session in this repo
  writes your prompts into `.sgt/`. Harmless, but it is state, and step 8 clears it.
- `sgt_recall` — "why is this code the way it is", the tool the skill leads with — returns
  `{"rationale": [], "open_intents": []}` on this repo. Nothing here was authored through sgt, so
  there is nothing recorded to recall. On a repo whose own team used sgt it has content. Don't
  demo it here.

---

## 7. The workbench (about 2 minutes)

The visual surface is the VS Code extension (`editor/vscode`), version 0.6.2. It is **not part of
the sgt CLI** and needs a one-time install before a session.

**One-time setup** (done on this machine; `code --list-extensions` shows `semi-git.semi-git@0.6.2`):

```bash
cd /Users/r4yen/repos/semi-git/editor/vscode
npx --yes @vscode/vsce package --no-dependencies --out /tmp/semi-git-0.6.2.vsix
code --install-extension /tmp/semi-git-0.6.2.vsix
```

(`vsce` runs the repo's own `vscode:prepublish` → `npm run package` for you, so there is no
separate build step.)

If `npx` dies with an `EEXIST` / `EACCES` error out of `~/.npm/_cacache`, prefix it with
`npm_config_cache=/tmp/npmcache` — that is what worked here.

**In the session:**

```bash
code ~/repos/sgt-demo/EasyOCR
```

The extension activates on `.sgt/local/ideal.json`, and `.vscode/settings.json` in the demo repo
already points `sgt.path` at the right binary. Then either click the `semi-git` icon in the
activity bar (it gives you `Now`, `Features`, `Forks`, `Changes`, `Compositions`), or open the
command palette and run **`semi-git: Open Composition Workbench`** — it docks in the bottom panel
next to Terminal, the way GitLens's Commit Graph does.

> **Honesty note.** Unlike every other step in this document, the three bullets below were read off
> the extension's source (`editor/vscode/src/workbench.ts`) and its data layer was timed against
> the demo repo — they were not watched on screen. Open the workbench once yourself before you use
> this step in a session.

Three things to point at:

1. **The rail** — the same lanes and the same time axis as the terminal's `sgt log`, but
   scrollable and clickable. "Same graph, drawn properly."
2. **The playhead.** Drag it back along the commit axis and the code panel refolds to what the
   files looked like at that point. Nothing is checked out and nothing is written — it is a read.
   This is the thing the terminal cannot do and it is what to spend the two minutes on.
3. **Click a lane** and the inspector shows the same detail as `sgt show`, with the revert preview
   from step 4 attached to a button. Do not press it.

Two warnings:

- **The workbench shows 67 features, not 29.** It is driven by `sgt advanced compose --json
  --full`, and the zero-symbol filter is applied when the terminal *renders*, not to the JSON:
  `sgt log --tree --json` reports `feature_count: 67` too. So the rail has 38 lanes in it that own
  no code. Expect it and say it: "the panel isn't filtering the bookkeeping groups the terminal
  hides."
- **First paint runs a refresh.** Its two backing reads are `sgt advanced compose --json --full`
  (2.2 s on this warm build) and `sgt log --tree --refresh --json` (0.65 s). The second is a write
  path — so open the workbench once during pre-flight, then re-check that `sgt log` is still
  banner-free.

If the extension is not installed and there is no time, skip this step. The four terminal steps
are the demo; the workbench is a bonus.

---

## 8. Reset, before the next participant

Step 5 dirties `.sgt` in ways `git status` cannot see: extra ops in `.sgt/ops`, an active
`plan_sessions.json`, `turns.json`, `local/hollow/*` predictions, `intent/prompts.json` from the
Claude Code hooks. `git checkout -- . && git clean -fd` does **not** undo any of that, and a
half-cleaned repo shows up as the staleness banner on the next facilitator's opening command.

Do all three lines:

```bash
cd ~/repos/sgt-demo/EasyOCR
git reset --hard 363afb184047ce452e436f4224f3098422df872e && git clean -fd
rm -rf .sgt && tar -xf .sgt-pristine.tar
```

`.sgt-pristine.tar` is a snapshot of `.sgt` taken by the build script at the end of a clean build
(about 170 MB, excluded from git). Restoring it is seconds; the alternative is the 10-minute
rebuild.

Then re-run pre-flight checks 1–6. Verified after a full step-5 rehearsal: this returns
`2319` ops, `275 saves`, `29 features`, `cached_map_is_current: True`, no staleness banner, no plan
or intent residue, clean tree, HEAD at the pin.

The surgical alternative — `sgt plan abandon <session>`, then `sgt intent done <id>` for each of
the two open intents it creates, then `git checkout -- .`, then `sgt log --refresh` — clears the
banner but **leaves the four extra ops in the store**, so the graph-integrity count creeps up by
one per rehearsal. Use the tar.

---

## Known rough edges

Everything here was observed on this build. A participant will find some of it; learn it from this
document, not from them.

**The graph is incomplete, and the gate says so.** `python scripts/check_graph_integrity.py
~/repos/sgt-demo/EasyOCR` prints, on the pristine build:

```
119 symbols an op touched are in no frontier and were never deleted
NOTE: 38 features carrying work but owning no symbol (finding 86, open, not a blocker)
409 symbols touched, 670 alive, 6 tombstoned, 670 placed in 67 leaf features
The graph is degenerate. Rebuild this repo; do not hand it to a participant.
```

The verdict line is expected output on this repo, not an instruction: a rebuild produces the same
119, because the cause is EasyOCR's own 2020 directory renames (the build script explains it at
length) and not the build. Concretely, the 119 include all of `easyocr/DBNet/backbones/`,
`easyocr/DBNet/decoders/`, `easyocr/detection_db.py`, `easyocr/cli.py`, `easyocr/export.py` and
most of `easyocr/utils.py`. Coverage is 19%.

**Consequences of that, in the order you will hit them:**

- `sgt find` indexes 254 of 670 symbols. `download_and_unzip` is missing; so is
  `Reader.readtext` (only the enclosing `Reader` is indexed). Step 3 leans on this.
- A symbol from the 119 does not resolve at all: `sgt show "easyocr/utils.py::download_and_unzip"`
  answers `is not a known feature, checkpoint, op, or symbol`. And a symbol that *does* resolve can
  still answer oddly — `sgt show "easyocr/easyocr.py::Reader"`, the class every EasyOCR user calls,
  reports `1 edit · last touched 2100d ago · in feature "OCR Model Assets"` and is forked. Address
  features by label and leave individual symbols out of the demo.
- **Step 5's new code never enters a feature.** After the rehearsal, the creation op for
  `filter_by_confidence` is in the ideal, but the `Reader.readtext` edit and the import edit are
  not — they are invalid, because their prerequisites are the rootless pre-rename versions. So
  `sgt why "easyocr/utils.py::filter_by_confidence"` answers `is not an op-id, a live symbol …`
  and `sgt show "Text Box Processing"` still lists only its two original symbols. The plan
  bookkeeping is right; the placement of the new work is not. Stop step 5 at `sgt plan status`.
- **`sgt save` does not work on this repo, and it fails two different ways.** `sgt save -m "…"` on
  the step-5 edits fails loudly with
  `put() would overwrite uncommitted changes: ['easyocr/easyocr.py', 'easyocr/utils.py']`. sgt
  reports 81 of 207 files as drift on a *clean, pristine* tree — its fold does not reproduce them
  — so it refuses to write them back. That is why step 5 commits with git and confirms with
  `sgt save --resolve-plan` instead. Do not type a bare `sgt save` in front of anyone.

  The second way is worse, and it is the one to actually fear on a shared screen. Edit a file sgt
  *can* reproduce — anything not in that 81 — and `sgt save` prints a **green tick over nothing**:

  ```
  $ git status --porcelain          #  M easyocr/cli.py
  $ sgt save -m "Add a --min_confidence option to the CLI"
  ✓ nothing to save -- no uncommitted ops
  $ echo $?                         # 0
  $ git status --porcelain          #  M easyocr/cli.py   — still dirty, HEAD unmoved
  ```

  The edit *was* mined: the op count goes 2319 → 2320 and the store gains one provenance-less op
  (`rework`, footprint `easyocr/cli.py::parse_args`). So the save both saw the work and reported
  there was none, exit 0. Reproduced twice on the pristine store, with no `resync` in between.
  Nothing recovers it except editing again after a reset.

- **Do not reach for `sgt advanced resync`, even though `sgt save`'s own error tells you to.** That
  error ends `(if you just rewrote git history -- reset/amend/branch -f -- run
  \`sgt advanced resync\`)`, so it is the obvious next thing to type. Measured here: it ran for
  **6 minutes 16 seconds**, re-derived `refs/heads/master` from 2,066 to 2,181 ops (+115), and
  brought the drift count down from 81 files to 71. `sgt save` then failed with exactly the same
  message. It is six minutes of dead air that fixes nothing and moves every op count in this
  document, including step 8's `2319`. If you have already run it, reset from the tar.

**Two forks print on every read.** `sgt log`, `sgt now` and `sgt log --focus` all open with the
two carriage-return filenames, and `sgt now`'s `→ next` is always "resolve fork" rather than
anything about the code. Get ahead of it in step 1.

**Terminal vs. JSON disagree on the feature count.** Rendered output says 29 features / 9
subsystems; `sgt log --tree --json`, `sgt log --json` and `sgt advanced compose --json` all say 67.
The revert preview says "66 other features". The VS Code workbench reads the JSON, so it shows 67.

**`sgt log --tree` says 10 subsystems where `sgt log` says 9** — the tree counts the root
`EasyOCR` node. Cosmetic; mentioned only so you do not have to work it out live.

**"reverted" in a checkpoint table means "no longer live"**, not "someone reverted it". Three of
the four checkpoints in step 2's table are marked that way on a repo where nothing was ever
reverted.

**Two big lanes are forked and cannot be reverted.** `sgt show "OCR Model Assets"` (202 symbols)
and `sgt show "Multilingual OCR Recognition"` (151) both end with `reverting this changes nothing`
and `⚠ this selection is forked: two versions of a symbol compete` — a third fork, on
`easyocr/recognition.py`'s torch import, that the `sgt log` banner does not mention. They are the
two longest lanes on the map, so they are the two a participant is most likely to ask about. Use
the features named in step 2 instead. `Multilingual OCR Recognition` is also mislabelled: its
symbol list opens with `README.md`, `custom_model.md` and `easyocr/DBNet/DBNet.py`, which is not
multilingual recognition.

**`sgt_recall` is empty here.** Nothing in EasyOCR was authored through sgt, so the tool the
`sgt-agent` skill leads with has nothing to return.

---

## After a rebuild

`FORCE=1 scripts/demo/build-easyocr-demo.sh` re-rolls the LLM labels *and* re-partitions the
graph. **Every feature label and every id in this document is then a guess.** Before the next
session:

1. `sgt log --tree` and replace the labels used in steps 2, 4 and 5 with whatever it now prints.
   Look for the same shapes: a small single-file detection lane, a `Reader`-bearing recognition
   lane, and one loss/decoder lane with a real cascade.
2. Re-run step 3's four queries and check which still land. The embedding index is rebuilt too.
3. Re-run the integrity gate and update the numbers in "Known rough edges".
4. Re-run the step-5 rehearsal end to end, then reset. The op ids in 5c will all be different.
