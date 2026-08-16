# Running the study

Everything a facilitator touches, in the order they touch it.

The study now runs from a website. The participant works through it from consent
to debrief; the facilitator watches from a console; the participant's machine
reports what happened by itself. Nobody reads a terminal aloud over a video call
and nobody types a number into a spreadsheet during a session.

- Participant: `https://sem-git.web.app/p/<their code>`
- Console: `https://sem-git.web.app/admin`
- Code lives in `web/`, the participant bundle in `scripts/study-bundle/`.

---

## 1. Once, before participant one

### Sign in

Open the console and sign in with Google. `ryanyen2@mit.edu` is named as the
owner in `web/firestore.rules`, so there is nothing to set up. Add other
experimenters from **Setup → Who else can see this console**; anyone added there
can read every participant's data and the answer key.

### Load the answer key

**Setup → Answer key → Load answer key JSON**, and pick
`docs/study/answer-key.json` from this repository.

It holds the 22 episodes, the quiz answers, the request keys and the rubrics. It
is stored where only signed-in experimenters can read it, and deliberately not
compiled into the site: a participant with devtools open must not be able to
read the quiz answers out of the JavaScript.

Without it the console still works, but scoring means looking things up in the
build logs by hand.

### Issue the session keys

**Setup → Session keys.** Three fields:

- An Anthropic API key. Claude Code uses it, through a profile inside the study
  folder.
- An OpenAI API key. sgt uses it for plain-English selections and feature naming.
- The model id, pinned for the whole study.

Issue keys **for this study**, with a hard spend cap. Never a personal key. They
are readable by anything holding a participant link, which is the price of the
setup script fetching them automatically so that nobody pastes a key by hand.

Revoke them per participant from the Participants tab as each session ends, and
revoke them at the provider too. The button here only marks them revoked.

### Fill in the participant-facing settings

**Setup → Participant-facing settings**: support email, compensation wording,
protocol number, the consent information sheet, and four bundle download URLs.

Name the bundle files neutrally. The participant sees the URL, and a filename
with `sgt` in it tells them which setup is the new one.

### Build the four bundles and publish

```bash
scripts/publish-study.sh
```

Builds the four bundles, builds the site, deploys, then fetches each bundle back
off the live site and checks its size against the file it just built.

**`npm run build && firebase deploy` does not rebuild the bundles.** It builds
the *website*; the bundles are separate artefacts the deploy copies out of
`web/public/bundles/` because they happen to live in the static directory. Change
sgt, deploy, and you get a fresh site handing out the previous tool, with nothing
on screen to say so. That is what this script exists to prevent, and it refuses
to run at all on a dirty tree, because a wheel built from uncommitted code is a
build no one can name afterwards.

Use `--site` when you have only changed the website, and `--dry-run` to build
without publishing.

Four, not one per participant. Everything specific to a person -- which half
they are on and the keys for their session -- is fetched by the setup script
from their code. Each build refuses to ship a project whose tests do not pass,
pre-warms the sgt history view so the participant's first command is fast, and
includes a throwaway practice repository that is not one of the two study
projects.

Upload them somewhere with a stable link, then paste each link into the matching
slot under Setup.

### Create the cohort

**Participants → Create 12.**

Twelve records, assigned round-robin across the four counterbalancing groups, so
any prefix of the cohort is still balanced. If the study stops at eight, those
eight are still two per group. Each gets a 24-character access code; the link is
the only credential, so treat it as one.

Type each participant's email into the roster as you recruit them, and copy their
link to send.

---

## 2. A day before each session

Send them their link and this:

> Before our session, please open this link and work through the first few pages:
> a consent form, a few questions about your background, and a setup step that
> installs everything on your machine. The setup takes a few minutes and
> downloads its own Python, so it will not change anything else on your laptop.
> Stop when you reach the practice page. If anything goes red, tell me rather
> than trying to fix it, and we will sort it out before the session rather than
> during it.

You will see them arrive in **Live** as they go.

The setup step ends with a checklist that fills in by itself: Python, the
project's tests, the history tool, the assistant profile, the assistant key, and
one real round trip to the assistant. That last check is the one that catches a
key that is present but wrong, which is otherwise invisible until the session
starts.

---

## 3. During the session

Keep **Live** open. It shows, per participant, which step they are on, the
countdown on the request they have open, whether their browser is connected, and
whether their machine is still reporting, with the last two dozen recorded
actions.

If "their machine" goes red, ask them to check the session shell is still open
and run `study-sync`. Their local log is safe either way; you are just flying
blind until it reconnects.

What has not changed: call the time at halfway and at two minutes left, say "we
are testing the setups, not you" often, and keep them talking. The site handles
the clock, not the conversation.

**Pauses.** If you interrupt them, or something breaks, have them press *Pause
the clock* and pick a reason. Analysis uses active time. Pilot 1 lost a request
to a tool failure with no record of how long the recovery took.

**A locked-out link.** If they clear their cache, switch browsers, or open a
private window, their link refuses to reopen. That is deliberate, and stops one
link being used by two people. Open their record and press **Release link**.

**Starting someone over.** Each roster row has **Reset** and **Delete**, at any
status. Reset wipes everything they did — responses, requests, events, devices,
keys, scores, notes — and puts them back at step one with the *same link and the
same condition order*, which is what keeps the cohort balanced. Delete removes
the person entirely. Both count what they are about to destroy and say so before
you confirm.

Reset is almost always the one you want: a pilot you are running again, a
session abandoned halfway, someone who has to reschedule. Delete is for a record
created by mistake.

**Nothing is lost by leaving the site.** Questionnaire answers, request answers
and interview notes are mirrored into the browser on every keystroke and written
through on a debounce, so a closed tab, a refresh, a crash or a dropped network
costs at most the last keystroke. Unsaved *scoring* is kept locally but never
written through — a half-finished rubric must not enter the data — and is
offered back the next time you open that request.

---

## 4. Scoring

Open a participant, then **Requests & scoring**. Each request shows what they
did, how long it took, whether they hit the cap, their own answer, and the
ground truth beside it.

For requests 2, 3 and 4, run the scorer and paste its output in:

```bash
python3 scripts/score_study_repo.py ~/study/p07/work \
    --baseline ~/repos/sgt-study/coursecraft \
    --expect-removed waitlist,promotion,notify \
    --expect-gone waitlist,notices
```

The output is kept verbatim as the evidence behind the number. Record which of
the four outcomes happened, including "tests pass but the app will not start",
which is why the scorer starts the program at all.

**Quiz & summary** grades the five questions against the key and the summary
against the 22-episode checklist. Three numbers come out of the summary:
episodes covered, causal links stated correctly, and confident claims that are
false. Coverage alone rewards listing; the three together separate remembering a
list from having built a theory, which is the whole point of RQ3.

**Interview** holds the probes with timestamped notes. Ask the fourth probe --
"what did you wish you could ask the history?" -- **before** they compare the
setups. Both pilots answered it with something close to what sgt does, one of
them from inside the git half, and that is worth protecting.

Two coders, 25 percent double-coded, negotiated agreement. See
`docs/study/protocol.md` §5.7.

---

## 5. Results

**Results → Compute from data** reads every participant's raw events and builds
the analysis. Nothing is precomputed: the raw stream is the record, and every
number is a pure function of it, so changing how a measure is defined is a code
change and a recompute rather than a lost measurement.

Three figures, each exporting to SVG at publication quality with fonts as text:

1. **What the two setups felt like.** The ten perception items as diverging
   stacked bars, with paired mean differences and 95% studentized-bootstrap
   intervals.
2. **What people managed to do.** Paired estimation plots for the four scored
   outcomes. Every participant is a line. Twelve slopes shown individually is
   the honest way to plot twelve people.
3. **How the work was done.** Where the time went across normalized request
   time, plus the action bigrams that most distinguish the conditions by
   weighted log-odds. This is the figure that answers "did they just use the AI
   more".

Three CSV exports underneath: one row per participant per condition for the
mixed models, one row per request, and the coded action stream for the
qualitative pass.

**Show example data** fills every figure with a synthetic cohort of twelve. Use
it to check the figures and the exports before the first session, not after.

---

## 6. Rehearsing

Two ways, for two different questions.

### A pilot record, on the real site

**Participants → Add pilot.** You get `X01`, with a real link, real keys and the
real bundle. It runs the identical flow. It is kept out of the analysis by a
field on the record, not by a naming convention, so:

- It is absent from Results unless you explicitly tick *Include the pilot
  records*, and ticking that puts a warning across the whole page.
- It is absent from the cohort counts and from the group balance.
- `Create 12` still produces exactly P01..P12 however many pilots you ran first.
  Pilots number themselves separately (`X01`, `X02`) from their own ordinal band.
- The participant's own page shows a **rehearsal** badge on every step, so a
  pilot link handed out by mistake is visible to them, not just to you.
- It stays deletable after it has been opened, unlike a real record.

Use this to check the parts that only exist on the real site: Google sign-in, the
live bundle download, the real keys reaching a real machine.

### Emulators, touching nothing

```bash
# terminal 1
java -jar ~/.cache/firebase/emulators/cloud-firestore-emulator-v*.jar \
    --host=127.0.0.1 --port=8080
# terminal 2
firebase emulators:start --only auth --project sem-git
# terminal 3
cd web && VITE_USE_EMULATOR=1 npm run dev
```

Every page then carries an orange "Rehearsal mode" banner. Nothing reaches the
real study, and there is nothing to clean up afterwards. Every rehearsal that
has to be cleaned up is a chance to delete the wrong thing.

Tests:

```bash
cd web && npm test                                  # rules, analysis, figures
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
    python3 scripts/study-bundle/tests/test_telemetry.py   # the recording path
```

---

## 7. What the participant's machine records

They run one command:

```bash
bash install/setup.sh <their code>
```

which installs its own Python, builds the project environment, installs the
history tool where the condition has one, installs Claude Code if it is missing,
fetches their assignment and keys, and runs the checks.

Then everything happens inside `./bin/study-shell`, which:

- points Claude Code at a profile **inside the study folder**, so their own
  account, settings and billing are never involved, and unsets any key of their
  own that might be in the environment;
- puts logging wrappers for `git`, `sgt`, `pytest` and `python` ahead of the
  real ones, which record the command and the exit code and change nothing else;
- runs a background sync so the console is never more than twenty seconds behind.

Claude Code hooks record prompts verbatim, tool calls, and turn boundaries. They
run asynchronously, so telemetry cannot make the assistant feel slow, and a
broken hook cannot block a session.

The local log is the record of truth and upload is a copy. Every event is
content-addressed and Firestore refuses to overwrite one that has landed, so
running the sync five times uploads each event once.

At the end: `study-sync --final`, then `study-cleanup`, which refuses to delete
anything until everything has been delivered.

### If a bundle is handed out wrong

The setup script checks the folder against the participant's assignment and
refuses to configure itself if they do not match. Working from the wrong folder
produces a session that looks perfectly normal and is unusable, and it is only
ever found during analysis.

### Things that still go wrong

- **`claude: command not found` after setup.** Their shell has not picked up
  `~/.local/bin`. New terminal tab, then `./bin/study-shell`.
- **`uv: command not found`.** Same cause, same fix.
- **Tests fail during setup.** Do not run the session. Rebuild the bundle and
  check it yourself first.
- **They wedge the project.** Note the time, pause the clock with reason "tool
  failure", have them unpack a spare copy, skip to the next request, and mark
  that request stopped by a tool failure. Keep one spare bundle per condition
  ready.
- **Nothing arrives from their machine.** The log on their disk is complete.
  Collect `telemetry/events.jsonl` by hand and it can be imported later.

---

## 8. End of session checklist

- Revoke both keys, in the console and at the provider.
- Confirm **Hand over your data** shows both halves delivered.
- Confirm they ran `study-cleanup`. The projects get reused.
- Save the screen recording against the participant label, not their name.
- Score requests 1 and 4 while the session is fresh.
