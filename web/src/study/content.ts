// Participant-facing prose: the welcome, the project brief, and the two
// practice sheets.
//
// Taken from docs/study/materials/ so there is one wording, not two. The only
// edits are the ones the bundle made necessary: the practice sheets no longer
// explain `../bin/sgt`, because the study shell puts the right binary on PATH,
// and the timings match docs/study/protocol.md §4.

import type { Condition, Project } from '../lib/types'
import { BLOCK_CAP_MIN } from './tasks'
import { TOTAL_ESTIMATE_MIN } from './flow'

// The numbers in the schedule below are read from the step list and the card
// caps rather than written out. The old copy said 110 minutes while the steps
// added up to 113, which is the harmless-looking version of a promise the
// session cannot keep.
//
// The figure at the top is deliberately rounder and more generous than the sum:
// it is what someone blocks out in a calendar, and it has to hold with breaks,
// a slow install, and a facilitator explaining something twice. A test keeps it
// above the computed total, so adding a card cannot quietly overrun the promise
// the participant agreed to.
export const PLAN_FOR = 'two and a half hours'

export const WELCOME_MD = `
Thanks for taking part. Plan for ${PLAN_FOR}, including breaks.

## What you'll do

- Work on a small program you have never seen. Someone built it over six weeks and then left.
- Read a short description of what it does, with no clock running.
- Handle three requests that have come in about it, and answer two short questions about what a past piece of work touched.
- Do that twice, with two different setups for looking at the project's history, on two different projects.

We are comparing the two setups. We are not testing you. If something confuses you, that is the most useful thing you can tell us.

## Please think out loud

- Say what you are about to do, and what you expect to happen.
- When something surprises you, say what you expected instead.
- When you don't understand what you are looking at, say so.

We record the screen and audio. You can ask us to stop at any time.

## What you can use

- Any tool on the machine.
- An AI coding assistant, set up and paid for by us. Ask it for anything, including running commands. Use it as much or as little as you normally would.

## How the time goes

| Minutes | What |
|---|---|
| 8 | Consent and a few questions about your background |
| 10 | Setting up your machine |
| 10 | A practice project |
| 5 | Reading about the project you are taking over |
| ${BLOCK_CAP_MIN} | Five cards: three requests, and two questions about what a past piece of work touched |
| 6 | Three short questionnaires |
| 15 | Setting up the second project, and practice again |
| 5 | Reading about the second project |
| ${BLOCK_CAP_MIN} | The same five cards, on the second project |
| 6 | Questionnaires again |
| 5 | Comparing the two |
| 3 | Handing your data over |

That is about ${TOTAL_ESTIMATE_MIN} minutes of work. The rest is breaks.

Every card has its own time limit, and you can see it counting down. The clock
only starts once you have read about the project, so there is no rush before
that.

- Stopping in the middle is fine.
- Finishing early is fine.
- We expect people to run out of time on some of them. That is a normal result, not a problem.

## One thing to know

Nothing you do can break anything that matters. Every project is a fresh copy. If you get one into a state you can't get out of, say so and we'll reset it. Getting stuck is information for us, so please don't hide it.

## Your own machine stays untouched

The setup step installs everything inside one folder and uses its own Python. It does not change your shell, your global packages, or your existing AI assistant account. The assistant runs on a key we issue for this session and revoke afterwards, so nothing is billed to you.
`.trim()

// ---------------------------------------------------------------------------
// The project brief
// ---------------------------------------------------------------------------
//
// Read with no clock running, between the practice and the requests.
//
// It exists because pilots met the codebase for the first time with a countdown
// already going, and spent the first four minutes of a seven minute request
// working out what the program was for. That is not the thing we are measuring,
// it is the same cost in both conditions, and it came out of the budget for the
// thing we are.
//
// It describes the product, not the code. No file names, no function names, no
// module layout, and nothing about how any of it was built -- all of that is
// what the requests are about, and handing it over here would answer request one
// on the way past.

export const PROJECT_BRIEF: Record<Project, string> = {
  coursecraft: `
You are taking over **coursecraft**. Riley Chen built it over the last six weeks, partly by working with an AI assistant, and has now left the team.

## What it is for

A university department runs course registration by hand every term: a spreadsheet of who is in which class, a second one for room bookings, and a lot of email. coursecraft replaces that. It is a command line tool. One person in the department office runs it, and everything it knows lives in a single file on disk.

## Who uses it, and how

Someone in the office types commands at a terminal. There is no web page and no login. A normal term looks like this:

- At the start of term they add the **courses** the department is running, and for each course the **sections** it is taught in — a section is one timetabled group, with a teacher, a room, a weekly time slot, and a cap on how many people fit.
- Students get added to the system, then **enrolled** into sections.
- When a student wants out, they are **dropped** from a section, which frees the seat.

## What it refuses to do

Most of the value is in what it stops you doing by accident. When someone is enrolled, the app checks that:

- the section is not already full,
- the student has passed whatever the course requires first,
- and the new section does not clash with something else in their week.

A section that ends at 10:30 and one that starts at 10:30 do not clash. That was a deliberate decision.

## The rest of it

- **Waitlists.** When a section is full a student can join a queue instead. When a seat frees up the next person in the queue gets it, and a notice is left for the office to pass on.
- **Search.** Find courses by typing part of a name or a topic.
- **Views and exports.** One student's week, one teacher's timetable, the whole catalogue as a spreadsheet, and a count of how full everything is.
- **A room audit**, which lists rooms that got double booked.

## Its condition

It works and it has a test suite that passes. It has never had a second maintainer.
`.trim(),

  confplan: `
You are taking over **confplan**. Sam Park built it over the last six weeks, partly by working with an AI assistant, and has now left the team.

## What it is for

A conference committee plans its two-day program by hand every year: a spreadsheet of who is booked into which talk, a second one for room bookings, and a lot of email. confplan replaces that. It is a command line tool. One person on the committee runs it, and everything it knows lives in a single file on disk.

## Who uses it, and how

Someone on the committee types commands at a terminal. There is no web page and no login. A normal year looks like this:

- While the program is being built they add the **talks** that were accepted, and for each talk the **sessions** it is given — a session is one scheduled slot, with a speaker, a room, a time on one of the two days, and a cap on how many people fit.
- Attendees get added to the system, then **registered** into sessions.
- When an attendee wants out, they are **unregistered** from a session, which frees the seat.

## What it refuses to do

Most of the value is in what it stops you doing by accident. When someone is registered, the app checks that:

- the session is not already full,
- the attendee has been to whatever the talk expects first,
- and the new session does not clash with something else in their day.

A session that ends at 10:30 and one that starts at 10:30 do not clash. That was a deliberate decision.

## The rest of it

- **Waitlists.** When a session is full an attendee can join a queue instead. When a seat frees up the next person in the queue gets it, and a notice is left for the committee to pass on.
- **Search.** Find talks by typing part of a title or a topic.
- **Views and exports.** One attendee's day, one speaker's schedule, the whole program as a spreadsheet, and a count of how full everything is.
- **A room audit**, which lists rooms that got double booked.

## Its condition

It works and it has a test suite that passes. It has never had a second maintainer.
`.trim(),
}

// The two practice sheets.
//
// Both are written editor-first. Pilots read a sheet made entirely of terminal
// commands, then met the requests inside an editor they had been given but
// never shown, and several never opened the history view at all -- which turns
// "does this representation help" into "did you find the panel".
//
// Every command, id, feature name and phrase quoted below is real in the
// warm-up repository that scripts/make-practice-repo.sh builds, and that script
// re-checks each of them at the end of a build. The previous sheet said things
// like `sgt show <id>` with no id, and `sgt find "the thing that formats
// dates"` against a repo with no dates in it, so the first thing a participant
// did in the sgt condition was watch search return nothing.

const TUTORIAL_GIT = `
Ten minutes on a practice project first. Ask anything now. Once the real requests start we can only answer questions about the requests themselves.

You already know git. This is not a lesson. It is here so that nothing on this machine surprises you later, and so that you have seen the editor before you need it.

## The practice project

Run \`study-practice\`. It puts you in a throwaway copy of a small shopping cart program. Nothing you do to it counts.

It has four pieces: \`cart.py\` (adding and removing things, and the total), \`discount.py\` (a percentage off, or a coupon code), \`receipt.py\` (printing a receipt), and \`shipping.py\` (what postage costs). Sixteen commits, and \`python -m pytest -q\` passes.

Every command on this sheet runs in the practice copy, and those four files only exist there. If \`ls\` shows anything else, you are in the real project: run \`study-practice\` and try again.

## 1. The editor first

\`\`\`
study-code
\`\`\`

That opens the practice project in VS Code with **GitLens** installed. Three things are worth finding now, because you will want them later:

- **Source Control** in the left bar, for what has changed and where you commit.
- **Commit Graph** — the GitLens icon in the left bar, or *GitLens: Show Commit Graph* from the command palette. The history as a graph you can click through.
- **File History** — right-click any file, *Open File History*. Blame also appears greyed out at the end of whichever line your cursor is on.

Open \`shipping.py\` and look at its file history. Four commits touch it. That is the shape of the thing you will be asked about later.

## 2. Look around, in the terminal

\`\`\`
git log --oneline
git log --stat
\`\`\`

## 3. Ask what one change was

Take a real one from that list:

\`\`\`
git show 44da4ad
git show 44da4ad -- shipping.py
\`\`\`

## 4. Follow one thing through time

This is the useful one. \`git log -S\` finds the commits where the number of times some text appears changed, so it tells you when something arrived and when it went away:

\`\`\`
git log --oneline -S "FREE_OVER"
\`\`\`

Three commits come back: free shipping over fifty arrived, then vanished inside a commit about per-item pricing whose message does not mention it, then came back. Try to see that same story in the Commit Graph.

Also worth having:

\`\`\`
git log -p -- shipping.py
git blame shipping.py
\`\`\`

## 5. Take something out, and put it back

\`\`\`
git revert 7e6e383
\`\`\`

Makes a new commit that undoes an old one. It can conflict if later commits touched the same lines; fix the conflict, or \`git revert --abort\`. Undo the revert itself with \`git revert HEAD\`, or throw the lot away with \`git reset --hard 7e6e383\`. This is the practice copy, so break it if you like.

Branches, for trying something you might throw away:

\`\`\`
git checkout -b try-something
git checkout main
git branch -D try-something
\`\`\`

## 6. Your assistant

\`claude\` starts it in the study shell. It knows git well and it can run commands for you, so "work out when free shipping stopped applying" is a perfectly good thing to type at it.

It can also plan before it acts. If you ask it to plan first, or use its plan mode, it lays out the steps it intends to take before touching anything. You do not have to try that now. It is worth knowing about for the second request.

Use the editor, the terminal, the assistant, or all three. Whatever you would normally do.

## Before we start

Tell us if any of that behaved differently from what you expected.
`.trim()

const TUTORIAL_SGT = `
Ten minutes on a practice project first. Ask anything now. Once the real requests start we can only answer questions about the requests themselves.

## What it is

\`sgt\` sits on top of an ordinary git repository. Git records which lines in which files changed. \`sgt\` records which functions and classes changed, and groups related work under a name. It calls those groups **features**.

Ten minutes will not make you fluent and we do not expect it to. Every command ends by printing what you might want to run next, so you can follow that rather than memorising anything.

## The practice project

Run \`study-practice\`. It puts you in a throwaway copy of a small shopping cart program. Nothing you do to it counts.

\`sgt\` has already read its history and found four features:

| Feature | What it is |
|---|---|
| **The Cart** | adding and removing things, and the total |
| **Discounts** | a percentage off, or a coupon code |
| **Receipts** | turning a cart into something you can print |
| **Shipping** | what it costs to post an order |

Those names are what you hand back to the commands below.

Every command on this sheet runs in the practice copy, and the four names above only exist there. If \`sgt log --tree\` shows anything else, you are in the real project: run \`study-practice\` and try again — nothing on this sheet applies to the real project's history.

## 1. The editor first

\`\`\`
study-code
\`\`\`

That opens the practice project in VS Code with the **semi-git** extension installed. Click the semi-git icon in the left bar and you get:

- **Now**, for where things stand and anything waiting on you.
- **Features**, the four above as a tree. Expand one to see what it covers.
- **Changes**, for what you have edited and not yet saved.

At the bottom, the **SGT Workbench** panel draws every feature as a row across time, so you can see which ones were being worked on at the same moment. There is a search box in its toolbar: type \`shipping\` into it.

Right-clicking a feature offers the same verbs as the commands below. **Toggle Feature Blame** puts the owning feature at the end of whichever line your cursor is on.

Open \`shipping.py\` with blame on. That is the shape of the thing you will be asked about later.

## 2. Look around, in the terminal

\`\`\`
sgt now           where things stand
sgt log           your saved work, newest first
sgt log --map     one row per feature, across time
sgt log --tree    just the four features and their handles
\`\`\`

In \`--map\`, the bars are how busy a feature was at that moment, and the \`@0\`, \`@1\`, \`@2\` chips underneath are its **checkpoints** — the chapters within one feature.

## 3. Ask what one thing is

Hand back a name, a function, or a save id. All three of these work:

\`\`\`
sgt show "Shipping"                what the feature covers
sgt show cart.py::total            what one function belongs to
sgt show 44da4ad                   what one save did
\`\`\`

Try the first. It tells you it covers five things in two files, lists the four saves that built it, and says how many edits removing it would take with it.

For a feature's chapters:

\`\`\`
sgt log --focus "Shipping"
\`\`\`

## 4. Find something when you do not know its name

Describe it:

\`\`\`
sgt find "the thing that works out postage"
\`\`\`

It ranks features, saves and functions against your words and hands you back the ids. The search box in the workbench toolbar does the same thing.

## 5. Record a change

Edit anything — a function, or just the README — then:

\`\`\`
sgt save -m "what you changed, in your own words"
\`\`\`

Your words become the name of that work, and it tells you which feature the change landed in. Do it once now so you have seen it happen.

## 6. Take something out, and put it back

Do this whole sequence. It is the most useful thing in these ten minutes.

\`\`\`
sgt revert "Receipts"
\`\`\`

Nothing has happened yet. That was a preview, and three things in it are worth reading: which chapters would go, that it removes 14 edits across 2 files, and the line saying **3 other feature(s) unchanged**. Now do it:

\`\`\`
sgt revert "Receipts" --yes
python -m pytest -q
\`\`\`

\`receipt.py\` and its tests are gone, and the other nine tests still pass. Put it back:

\`\`\`
sgt undo
python -m pytest -q
\`\`\`

Eleven again. \`sgt undo\` reverses the last thing sgt did; \`sgt restore "<name>"\` brings back something removed longer ago.

You can also take out one chapter rather than a whole feature:

\`\`\`
sgt revert Shipping@2
\`\`\`

Preview first is the rule everywhere, including in the editor.

## 7. Your assistant

\`claude\` starts it in the study shell. It can drive this tool as well as the shell, so "what came along with free shipping over fifty" and "take the receipts out" are both things you can just type at it. The workbench paints what a change would do to the graph while it happens.

It can also plan before it acts. Ask it to plan first, or use its plan mode, and it lays out the steps it intends to take before touching anything; \`sgt\` records that plan next to the work, so afterwards you can compare what it said it would do with what it did. You do not have to try that now. It is worth knowing about for the second request.

## 8. Help

\`\`\`
sgt --help
sgt <command> --help
\`\`\`

## Before we start

Tell us if anything printed something you could not make sense of.
`.trim()

export function tutorialFor(condition: Condition): string {
  return condition === 'git' ? TUTORIAL_GIT : TUTORIAL_SGT
}

export const TASK_PREAMBLE = (app: string, maintainer: string, blurb: string) =>
  `
You are the new maintainer of **${app}**, the program you just read about — ${blurb}. ${maintainer} built it over the last six weeks, partly by working with an AI assistant, and has now left the team.

You have the code, its full history, and your assistant. Three requests, in order, about twenty minutes in total. Each has its own clock and running out of time on one is a normal result.

Tell us what you are thinking as you go.
`.trim()

export const HANDOVER_MD = `
Almost done. Two things and you can close everything.

## 1. Send us what your machine recorded

In the study shell, run:

\`\`\`
study-sync --final
\`\`\`

It uploads anything still waiting and prints a confirmation. The tick below turns green when we have it. If it stays red for more than a minute, tell your facilitator rather than retrying, because the log on your disk is safe either way and we can collect it another way.

## 2. Delete the folders

Once the tick is green:

\`\`\`
study-cleanup
\`\`\`

That removes the two project folders, the session's API keys, and the editor profile it made, from your machine. The projects get reused with other participants, so please do run it.

Your own assistant and your own editor are untouched. Nothing was installed outside the study folder.
`.trim()

export const DEBRIEF_MD = `
That is everything. Thank you.

## What this was about

Version control records history as lines in files. When you work with an AI assistant you describe what you want in sentences, and then the record of what you did comes back to you as diffs. We built a tool that records history as the pieces of work someone meant to do, and we wanted to know whether that helps a person who arrives afterwards.

One of the two setups you used was that tool. The other was ordinary git. We deliberately did not say which was which, and we asked the same three requests of both, because we are measuring the difference between the two representations and not the difference between you and anyone else.

## What happens to your data

Your recordings, your answers, and the log of commands and prompts from your machine are stored under a participant code, not your name. Your name appears only on the consent record, which is kept separately. Results are reported in aggregate. If you ticked the optional consent line, a short quote might appear, with nothing in it that identifies you.

If you want your data removed, email us with your participant code and we will delete it. No reason needed.
`.trim()
