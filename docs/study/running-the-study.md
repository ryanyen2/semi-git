# Running the study

> **Partially out of date (2026-08-25).** The flow it describes is protocol
> v1's. The website now implements the four-stage design of `protocol-v2.md`;
> the mechanics (console, bundles, uploads) are unchanged, but the step list,
> the timing, and the scoring pages are not.

This is the step-by-step guide for facilitators, in the order you will need
things. Everything here assumes you are using the study website.

The study runs from a website. The participant works through it from consent to
debrief. The facilitator watches from a web console. The participant's machine
reports what happened automatically. Nobody reads a terminal aloud over a video
call, and nobody types numbers into a spreadsheet during a session.

**Links:**

- Participant page: `https://sem-git.web.app/p/<their code>`
- Facilitator console: `https://sem-git.web.app/admin`
- Website code: `web/`
- Participant bundle code: `scripts/study-bundle/`

---

## 1. One-time setup (before participant one)

These steps only need to happen once, before you run any sessions.

### Sign in to the console

Open the console and sign in with Google. The account `ryanyen2@mit.edu` is
listed as the owner in `web/firestore.rules`, so it works immediately with no
extra setup. To give other experimenters access, go to **Setup → Who else can
see this console**. Anyone you add can see all participant data and the answer
key.

### Load the answer key

Go to **Setup → Answer key → Load answer key JSON** and select the file
`docs/study/answer-key.json` from this repository.

The key holds, per project: every commit in each testbed's history; the measured
behaviour set the two scored checklists share (stage 2 predicts it, stage 3
reports it, so `gain` compares like with like); the accepted-strings list for
stage 2's locate; and the pages stages 3 and 4 are scored against. There is no
entry for stage 1 — it asks nothing with a right answer. It is stored where only
signed-in experimenters can read it, and is deliberately *not* compiled into the
website JavaScript: the questions ship in the bundle the participant's browser
downloads, so answers living beside them would be readable from the page source.

The upload validates before it stores. It refuses a key with no behaviour set
for a scored checklist, one that answers only one of the two projects, one naming
a behaviour the checklist does not offer, and one naming every behaviour. Those
are the shapes that upload clean and score nothing, which is indistinguishable
from a study that did not ask the question.

**Regenerate the key whenever the bundles are rebuilt.** Feature ids, group ids
and commit shas all move, and a key naming work the repository no longer contains
scores everyone as wrong. It is generated, not written:

```bash
# against the two sgt bundles' work/ directories, not the source testbeds:
# the bundle build ends with `sgt log --rebuild`, and the key has to name the
# graph a participant will actually see
ANSWER_KEY_BASELINES=~/repos/sgt-study \
python3 scripts/study/harvest/write_answer_key.py \
    <unpacked-bikecount-b>/work <unpacked-footfall-b>/work docs/study/answer-key.json
```

It measures both targets for real — removes each on a copy, runs the app's own
check, re-renders every page, and maps what moved onto the eleven options — and
exits non-zero rather than writing a key for a target whose removal kills the
app.

Without the key loaded, the checklists are not scored at all. They show as
unscored rather than as wrong, which is deliberate — a stage answered before the
key was loaded is a different thing from a wrong answer — but it means the
figures have a hole in them until you load it.

### Issue the session API keys

Go to **Setup → Session keys**. There are three fields:

- **Anthropic API key.** Claude Code (the AI assistant) uses this key. It runs
  through a profile inside the study folder, not the participant's own account.
- **OpenAI API key.** The sgt tool uses this for plain-English feature selection
  and feature naming.
- **Model ID.** The Claude model to use, pinned for the whole study so every
  participant runs the same model.

**Important:** Issue keys specifically for this study, with a hard spend cap.
Never use a personal key. The keys are readable by anything that has a
participant link — that is the trade-off of having the setup script fetch them
automatically so nobody has to paste a key by hand.

After each session, revoke the participant's keys from the **Participants** tab.
Also revoke them at the API provider (Anthropic / OpenAI). The button in the
console only marks them as revoked in our system.

### Fill in participant-facing settings

Go to **Setup → Participant-facing settings** and fill in:

- Support email address
- Compensation wording
- Protocol number (from ethics approval)
- Consent information sheet

There are also four bundle download URL slots. **Normally leave them blank.**
`make-study-bundle.sh` writes into the site's own static files, so a deployed
bundle is already served at `/bundles/study-<project>-<a|b>.tgz`, which is where
the setup page looks. Fill one in only if you are hosting that bundle somewhere
else.

The `a` and `b` in those filenames are deliberate. The participant can see the
download URL, and a filename containing "sgt" would tell them which condition is
the new tool.

### Build the four bundles and deploy

Run this command from the repository root:

```bash
scripts/publish-study.sh
```

This script builds all four bundles (2 conditions × 2 projects), builds the
website, deploys everything, then fetches each bundle back from the live site to
verify its size matches what was just built.

**Why not just `npm run build && firebase deploy`?** That only rebuilds the
*website*. The bundles are separate build artifacts that get copied from
`web/public/bundles/` during deployment. If you change sgt and deploy without
rebuilding the bundles, you get a fresh website handing out the *previous*
version of the tool, with nothing on screen indicating the mismatch. The publish
script exists to prevent this. It also refuses to run on a dirty git tree,
because a wheel built from uncommitted code produces a build that cannot be
identified later.

Use `--site` when you have only changed the website (not sgt or the bundles).
Use `--dry-run` to build everything without actually deploying.

**After a bundle rebuild, regenerate the answer key and load it again.** The
bundle build finishes with `sgt log --rebuild`, which can renumber features and
rename groups, so a key generated before it can name work by an id the shipped
bundle does not use. The command is in "Load the answer key" above.

There are four bundles total, not one per participant. Everything specific to a
person — which condition they are in and the API keys for their session — is
fetched by the setup script using their participant code. Each bundle build
verifies the project's tests pass, pre-warms the sgt history view (so the
participant's first command is fast), pins the editor extensions, and includes
the throwaway practice repository, which is separate from the two study
projects.

Watch the practice repository lines the build prints. In the sgt condition it
pins four feature names — The Cart, Discounts, Receipts, Shipping — and then
checks every handle the practice sheet quotes. If it warns that a name did not
stick or a handle does not resolve, the practice sheet is wrong on that machine,
and a participant will find that out in front of you. Fix it before the session
rather than during it.

The publish script deploys the bundles alongside the site and then fetches each
one back from the live URL to check its size, so there is nothing to upload or
paste in afterwards.

### Create the cohort

Go to **Participants → Create 12**.

This creates twelve participant records, assigned round-robin across four
counterbalancing groups. The round-robin assignment means any prefix of the
cohort is still balanced — if the study stops at eight participants, those eight
are still two per group. Each participant gets a 24-character access code. The
link containing this code is the only credential, so treat it like a password.

As you recruit participants, type each person's email into the roster and copy
their link to send to them.

---

## 2. A day before each session

Send the participant their link along with this message:

> Before our session, please open this link and work through the first few
> pages: a consent form, a few questions about your background, and a setup step
> that installs everything on your machine. The setup takes a few minutes and
> downloads its own Python, so it will not change anything else on your laptop.
> Stop when you reach the practice page. If anything goes red, tell me rather
> than trying to fix it, and we will sort it out before the session rather than
> during it.

You will see them appear in **Live** as they progress through the steps.

The setup step ends with a checklist that fills in automatically. It checks:
Python is installed, the project's tests pass, the history tool is working (in
the sgt condition), the assistant profile is isolated, the assistant key is in
place, and the assistant can actually answer a test message. That last check is
the one that catches a key that looks valid but is not — something that would
otherwise be invisible until the session starts.

---

## 3. During the session

Keep **Live** open in the console. For each participant, it shows:

- Which step they are on
- The countdown timer for their current stage
- Whether their browser is connected
- Whether their machine is still reporting (with the last two dozen recorded
  actions)

If the "machine reporting" indicator goes red, ask them to check that the
session shell is still open and run `study-sync`. Their local log is safe
regardless — you are just unable to see what they are doing until the connection
comes back.

**General facilitation:** Call the time at halfway and again at two minutes left.
Remind them often that "we are testing the setups, not you." Keep them talking
out loud. The website handles the clock; you handle the conversation.

### Pausing the clock

If you need to interrupt them, or if something breaks, have them press **Pause
the clock** and pick a reason from the list. The analysis uses active time only.
In Pilot 1, a stage was lost to a tool failure with no record of how long the
recovery took — this is why the pause feature exists.

### Locked-out links

If a participant clears their browser cache, switches browsers, or opens a
private/incognito window, their link will refuse to reopen. This is deliberate:
it prevents one link from being used by two people. To fix it, open their record
in the console and press **Release link**.

### Starting someone over

Each participant row in the roster has **Reset** and **Delete** buttons,
available at any status.

- **Reset** wipes everything the participant has done — responses, stage data,
  recorded events, device records, keys, scores, and notes — and puts them back
  at step one. It keeps the *same link and the same condition order*, which
  preserves the cohort's counterbalancing. This is almost always what you want:
  for a pilot you are running again, a session abandoned halfway, or someone who
  needs to reschedule.
- **Delete** removes the participant record entirely. Use this only for records
  created by mistake.

Both buttons count what they are about to destroy and display the count before
you confirm.

### Nothing is lost by leaving the site

Questionnaire answers, stage answers, and interview notes are saved to the
browser on every keystroke and written to the server on a short delay (debounce).
A closed tab, a page refresh, a browser crash, or a dropped network connection
costs at most the last keystroke.

The one exception: unsaved *scoring* work is kept locally in the browser but
never written to the server automatically. This is intentional — a half-finished
rubric must not enter the dataset. The next time you open that stage, it
offers to restore your in-progress scoring.

---

## 4. Scoring

Open a participant's record, then go to **Requests & scoring**. Each stage shows
what the participant did, how long it took, whether they hit the cap, their
answers, and the ground truth beside them.

The full answer key, and what each stage's numbers mean, is
`participant-materials.md` under "Scoring guide". This section is only the
mechanics.

### Stage 1 has nothing to score

No checklist, no key. It ends on three rating statements, which the console
records and rolls up as one mean. What stage 1 produces beyond that is your
notes; `participant-materials.md` lists what to watch for.

### Stages 2 and 3 score themselves

Both end in the eleven-item checklist, which the console scores as set F1
against `answer-key.json` and pairs with the confidence rating to compute
calibration. There is nothing to grade. **If the panel shows a checklist
unscored, the answer key is not loaded** — go back to section 1.

Stage 2 also asks the participant to name the work they found. Scoring that is
yours: the console shows their answer beside the list of strings the key accepts,
in both arms' vocabularies, and you mark it. The list is long on purpose — a
commit sha and a group name are both right — and it is not a substitute for
having listened to what they said aloud.

### Stages 3 and 4 are scored from the repository

Both change code, so neither can be scored from a form. Run the scorer against
the participant's copy and paste its output into the scoring field; the output is
kept verbatim as the evidence behind the score.

```bash
# stage 3: the work is out, and nothing else moved
python3 scripts/study/score_dashboard.py ~/study/p07/work \
    --expect ~/repos/sgt-study/footfall/.study/removed-pages \
    --target-pages hourly,monthly,overview,sides,yearly

# stage 4: every page matches the state before the removal
python3 scripts/study/score_dashboard.py ~/study/p07/work \
    --expect <the original snapshot>
```

Three results come back and stay separate: whether the app runs at all, whether
the pages the change was meant to reach now match, and whether anything else
moved. The third is the one the study is about. Do not fold them together — a
repository that will not render is a different outcome from a wrong one, and
version 1's scorer hid exactly that by counting passing tests.

`--target-pages` differs by project: `hourly,monthly,overview,sides,yearly` for
footfall, `hourly,monthly,overview,years` for bikecount. Both lists are in
`answer-key.json` under `s3.markers`, so read them from there rather than from
here.

### The post-half questionnaires

Nothing to score. UMUX-Lite and raw NASA-TLX are recorded as the participant
answers them and go straight into the analysis.

One thing to leave alone: the workload scales are marked on a rule of tick marks
with no number anywhere, and one of the six runs the other way from the rest.
That is the published instrument. If someone asks, point at the two words at the
ends of the rule and say nothing else.

### Interview notes

The **Interview** section holds the ten questions of the guide
(`protocol-v2.md` section 6.4) in four headed groups, each with space for
timestamped notes. Cmd/ctrl-Enter saves one; drafts survive a refresh, because
nothing else in the study records a conversation.

Ask them in order. Questions 1 and 6 ask for a comparison, so the interview
cannot start before both halves are done. Question 10 — "what do you think
version control should let you identify and act on directly?" — is asked about
version control and not about sgt: naming sgt's unit of work first makes
agreement the easy answer, and in both pilots participants reached for something
close to what sgt does on their own, which is only evidence if nobody put it
there.

Notes filed against an earlier guide's questions appear read-only at the bottom
under "Notes from earlier guides", so a rewrite of the guide does not quietly
take them off the screen.

Coding uses two independent coders, with 25% double-coded and disagreements
resolved through negotiated agreement. See `protocol.md` §7, "Qualitative
material", for the full procedure.

---

## 5. Results

Go to **Results → Compute from data**. This reads every participant's raw event
stream and builds the full analysis. Nothing is precomputed: the raw event
stream is the permanent record, and every number is computed from it as a pure
function. Changing how a measure is defined is a code change and a recompute,
not a lost measurement.

### The three figures

Each figure can be exported to SVG at publication quality (with fonts rendered
as text, not outlines):

1. **What the two setups felt like.** The per-stage rating statements as
   diverging stacked bars, one panel per condition, with paired mean differences
   and 95% bootstrap confidence intervals. Reverse-keyed items are recoded so
   that agreement always means better, and marked. Anything collected as a check
   on the design rather than as an outcome is left out.
2. **What people managed to do.** Paired estimation plots, one panel per
   measure: stage 1's rating mean (its only outcome — it asks nothing scored),
   stage 2's locate, `gain`, stage 3 and stage 4, and collateral damage. Every participant is a line connecting their score in each condition.
   Showing all twelve slopes individually is the honest way to visualize twelve
   people.
3. **How the work was done.** Where time was spent across normalized stage time,
   plus the action bigrams (two-step sequences) that most distinguish the two
   conditions, ranked by weighted log-odds ratio. This is the figure that answers
   "did they work differently, or just slower?"

### Data exports

Three CSV exports are available underneath the figures:

- One row per participant per condition — for the mixed-effects models
- One row per stage — for stage-level analysis
- The coded action stream — for the qualitative analysis pass

### Testing with synthetic data

**Show example data** fills every figure with a synthetic cohort of twelve
participants. Use this to verify the figures and exports look correct *before*
the first real session, not after.

---

## 6. Rehearsing

There are two ways to rehearse, for two different purposes.

### Option A: A pilot record on the real site

Go to **Participants → Add pilot**. This creates a pilot participant (labeled
`X01`, `X02`, etc.) with a real link, real API keys, and the real bundle. It
runs the identical flow that a real participant sees.

Pilot records are kept separate from real data:

- They are absent from **Results** unless you explicitly tick **Include the pilot
  records**, which puts a warning banner across the whole page.
- They are not counted in the cohort totals or group balance.
- **Create 12** still produces exactly P01 through P12 regardless of how many
  pilots you ran first. Pilots use their own numbering sequence (X01, X02).
- The participant's own page shows a **rehearsal** badge on every step, so a
  pilot link handed out by mistake is visible to the participant, not just to
  you.
- Pilot records remain deletable after they have been opened, unlike real
  participant records.

Use pilot records to check the parts that only work on the live site: Google
sign-in, live bundle downloads, and real API keys reaching a real machine.

### Option B: Local emulators (touches nothing)

Run these three commands in separate terminal windows:

```bash
# Terminal 1: Firestore emulator
java -jar ~/.cache/firebase/emulators/cloud-firestore-emulator-v*.jar \
    --host=127.0.0.1 --port=8080

# Terminal 2: Auth emulator
firebase emulators:start --only auth --project sem-git

# Terminal 3: Development server
cd web && VITE_USE_EMULATOR=1 npm run dev
```

Every page will show an orange "Rehearsal mode" banner. Nothing reaches the real
study database, and there is nothing to clean up afterwards. (Every rehearsal
that has to be cleaned up is a chance to accidentally delete the wrong thing.)

To run the tests:

```bash
cd web && npm test                                  # security rules, analysis, figures
python3 scripts/study-bundle/tests/test_sync_daemon.py  # when the pusher stops
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
    python3 scripts/study-bundle/tests/test_telemetry.py   # the recording pipeline
```

---

## 7. What the participant's machine records

### Setup

The participant runs one command:

```bash
bash install/setup.sh <their code>
```

This installs its own Python (via `uv`), builds the project environment,
installs the history tool (in the sgt condition), installs Claude Code if it is
not already present, fetches the participant's assignment and API keys from the
study website, and runs the setup checks.

### The session shell

After setup, everything happens inside `./bin/study-shell`. The session shell
does three things:

1. **Isolates the AI assistant.** It points Claude Code at a profile *inside the
   study folder*, so the participant's own account, settings, and billing are
   never involved. It also unsets any API keys the participant might have in
   their environment, so their own keys cannot be accidentally used and billed.

2. **Records commands.** It places logging wrappers for `git`, `sgt`, `pytest`,
   and `python` ahead of the real binaries on `PATH`. These wrappers record the
   command and its exit code, then pass everything through to the real program
   unchanged.

3. **Syncs data in the background.** A background process pushes recorded events
   to the study website every twenty seconds, so the facilitator's console stays
   current.

### What gets recorded

Claude Code hooks record: the full text of every prompt the participant sends,
every tool call the assistant makes, and turn boundaries. The hooks run
asynchronously, so telemetry cannot make the assistant feel slow, and a broken
hook cannot block a session.

### Data integrity

The local log file is the record of truth. Uploading to the website is a copy.
Every event has a content-based ID, and the server (Firestore) refuses to
overwrite an event that has already landed. Running the sync five times uploads
each event exactly once.

### End of session

At the end of the session: run `study-sync --final`, then `study-cleanup`.
The cleanup script refuses to delete anything until it has confirmed that
everything has been delivered to the server.

### If a bundle is handed out wrong

The setup script checks the bundle folder against the participant's assignment
and refuses to configure itself if they do not match. A session run from the
wrong bundle looks perfectly normal during the session but produces unusable
data — and the mismatch is only discovered during analysis. This is why the
check exists.

### Common problems and fixes

- **`claude: command not found` after setup.** The participant's shell has not
  picked up `~/.local/bin` where Claude Code was installed. Fix: open a new
  terminal tab, then run `./bin/study-shell` again.
- **`uv: command not found`.** Same cause, same fix.
- **Tests fail during setup.** Do not run the session. Rebuild the bundle and
  test it yourself first.
- **The participant wedges the project** (gets it into an unrecoverable state).
  Note the time, pause the clock with reason "tool failure", have them unpack a
  spare copy of the bundle, run `./stage N` for the next stage, and mark that
  stage as stopped by a tool failure. Every stage resets, so nothing is lost
  beyond the one they were in. Keep one spare bundle per condition ready.
- **Nothing arrives from their machine.** The log on their disk is complete
  regardless. Collect the file `telemetry/events.jsonl` from their machine by
  hand — it can be imported into the database later.

---

## 8. End-of-session checklist

- [ ] Revoke both API keys — in the console and at the provider (Anthropic /
  OpenAI).
- [ ] Confirm **Hand over your data** shows both halves as delivered.
- [ ] Confirm the participant ran `study-cleanup`. The study projects may be
  reused.
- [ ] Save the screen recording under the participant's label (e.g., P07), not
  their name.
- [ ] Score stage 2's locate and stages 3 and 4 while the session is still
      fresh in your mind.
