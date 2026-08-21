# The study website

Two surfaces on one Firebase project (`sem-git`, hosted at
`https://sem-git.web.app`):

- `/p/<code>` — the participant's whole session, consent to debrief.
- `/admin` — the experimenter's console: roster, live monitor, scoring,
  interview notes, results and figures.

The operator's manual is `docs/study/running-the-study.md`. The protocol every
question and measure comes from is `docs/study/protocol.md`. This file is about
the code.

## Layout

```
src/
  lib/
    firebase.ts      app init, anonymous and Google auth, emulator wiring
    types.ts         every Firestore document shape, in one place
    db.ts            live subscriptions, the autosave form, request timing
    stats.ts         bootstrap intervals, paired estimates, weighted log-odds
    svgExport.ts     publication-quality SVG, PNG and CSV export
  study/
    instruments.ts   every questionnaire, item wording fixed and versioned
    tasks.ts         the six requests, in both projects
    flow.ts          counterbalancing, and the 22 steps of a session
    taxonomy.ts      the nine-category action alphabet
    content.ts       welcome, practice sheets, handover, debrief
  analysis/
    pipeline.ts      raw events -> categorized actions -> per-request measures
    ngram.ts         sequence comparison and the time profile
    demo.ts          a synthetic cohort, for checking figures before session one
  participant/       the participant flow, one component per step
  experimenter/      the console
  charts/            the three paper figures
tests/               rules, analysis and figure tests
```

## Two rules the code follows

**The raw event stream is the record; everything else is derived.** No
preprocessing happens on the participant's machine, and nothing in Firestore is
an authoritative summary. `Results → Recompute` rebuilds every number from the
raw events, so redefining a measure is a code change rather than a lost
measurement.

**Nothing a participant enters may be lost.** Forms write to React state, to
localStorage, and to Firestore on a debounce, and flush again when the tab
hides. Firestore's offline cache queues writes through a dropped connection. You
cannot ask someone to feel the same way twice.

## Working on it

```bash
npm install
npm run dev          # against production Firestore -- careful
npm run build
```

### Rehearsal mode

Run the whole study against local emulators, with an orange banner on every page
so it cannot be confused with a session:

The Firestore emulator is a Java program, and the error it gives when Java is
missing names Java without saying where yours is. Homebrew's `openjdk` is
keg-only, so installing it does not put `java` on your PATH:

```bash
brew install openjdk                          # if you have not already
export PATH="/opt/homebrew/opt/openjdk/bin:$PATH"
java -version                                 # should print a version, not a link to java.com
```

Then two terminals:

```bash
# terminal 1: Firestore and Auth together
firebase emulators:start --only firestore,auth --project sem-git

# terminal 2
VITE_USE_EMULATOR=1 npm run dev
```

The console then offers a sign-in that skips Google. That branch is behind a
build-time constant, so it is compiled out of a production bundle entirely.

### Tests

```bash
npm test                                     # needs the Firestore emulator up (see above)
FIGURE_OUT=/tmp/figs npm test                # also writes the figures to look at
```

- `tests/rules.test.ts` — the security properties the study depends on: a
  participant cannot move their own condition, read the answer key, see their
  scores, or overwrite a telemetry event that has landed.
- `tests/analysis.test.ts` — the judgement calls in the pipeline, including the
  ones that would silently change a result if they were wrong.
- `tests/charts.test.tsx` — each figure renders to a self-contained SVG with no
  stylesheet dependency and text still text.

The participant bundle has its own suite:

```bash
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
    python3 ../scripts/study-bundle/tests/test_telemetry.py
```

## Deploying

```bash
npm run build
firebase deploy --only firestore:rules,firestore:indexes,hosting --project sem-git
```

`firestore.rules` names the study owner directly. Change it there and in
`OWNER_EMAIL` in `src/experimenter/ExperimenterApp.tsx`; the two must agree.
Everyone else is added from **Setup → Who else can see this console**.
