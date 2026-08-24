// Participant-facing prose: the welcome, the project brief, and the two
// practice sheets.
//
// Taken from docs/study/materials/ so there is one wording, not two. The only
// edits are the ones the bundle made necessary: the practice sheets no longer
// explain `../bin/sgt`, because the study shell puts the right binary on PATH,
// and the timings match docs/study/protocol.md §4.

import type { Condition, Project } from '../lib/types'
import {
  BLOCK_CAP_MIN,
  CARD_COUNT,
  SCENARIO,
  requestHeading,
  taskCards,
} from './tasks'
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

- Take over a small web dashboard you have never seen. Someone built it over six weeks, mostly by describing what she wanted to an AI assistant, and then left.
- Read a short description of what it does, with no clock running.
- Work through four cards: see what it does today, find the piece of work behind one of its behaviours, take that work out, and put it back.
- Do that twice, with two different setups for reading the project's history, on two different projects.

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
| ${BLOCK_CAP_MIN} | ${CARD_COUNT} cards: see a defect, find what caused it, take it out, then remove a feature |
| 6 | Three short questionnaires |
| 15 | Setting up the second project, and practice again |
| 5 | Reading about the second project |
| ${BLOCK_CAP_MIN} | The same ${CARD_COUNT} cards, on the second project |
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

/**
 * The same welcome, as the printed handout in `docs/study/materials/00-welcome.md`.
 *
 * That file used to be written by hand alongside this one. It said "about two
 * hours", "three requests", and a table adding up to 100 minutes, while this said
 * two and a half hours, five cards, and 129. Both were handed to the participant,
 * so which one they happened to read decided what they thought they had agreed to.
 * `npm run gen:materials` writes the file from here, and a test fails if it drifts.
 *
 * The only difference is the title: the website renders its own, a printed page
 * needs one in the text.
 */
export const HANDOUT_MD = `# Welcome\n\n${WELCOME_MD}\n`

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
  bikecount: `
You are taking over **bikecount** from Dana Whitfield, who has left the transport data team.

## What it is for

There is a counter on the Fremont Bridge in Seattle with a sensor on each sidewalk. It counts bikes crossing, one number per hour, going back to 2012. The city publishes the file.

bikecount reads that file and puts it on a web page. Open it in a browser and you get the busiest day on record, a chart of what time of day people ride, totals by month and by year, and a comparison of the two sidewalks. There is no login and no database. It reads the csv off disk every time you load a page.

Three people use it. Its numbers go into the cycling team's quarterly report.

## How it was built

Dana built it over six weeks, mostly by describing what she wanted to an AI assistant and checking the result. Each piece of work is one afternoon's job, and the history says what each one was for.

## What is in it

- **Hour of day.** What time people ride, weekdays and weekends apart.
- **By month and by year.** Totals over time, and a table for the front of the report.
- **East against west.** Whether the two sidewalks are balanced.
- **Quiet days.** A list of days that are nothing like a normal day, such as the February 2019 snowstorm and Christmas. Dana started keeping it because those days kept being read as real drops in cycling.
- **A csv download**, so people stop asking for the numbers by email.

## How to run it

    python3 -m bikecount.server

Then open http://localhost:8000. To check nothing is broken:

    python3 check.py

It renders every page and fails loudly if one of them throws.

## Its condition

It works. The smoke check passes. It has never had a second maintainer.
`,
  footfall: `
You are taking over **footfall** from Dana Whitfield, who has left the transport data team.

## What it is for

The city has a sensor on each side of the crossing between Southern Cross station and the Collins Street offices in Melbourne. They count people walking past, one number per hour, going back to 2013. The council publishes the file.

footfall reads that file and puts it on a web page. Open it in a browser and you get the busiest day on record, a chart of what time of day people walk past, totals by month and by year, and a comparison of the two sides. There is no login and no database. It reads the csv off disk every time you load a page.

Three people use it. Its numbers go into the transport committee's quarterly paper.

## How it was built

Dana built it over six weeks, mostly by describing what she wanted to an AI assistant and checking the result. Each piece of work is one afternoon's job, and the history says what each one was for.

## What is in it

- **Hour of day.** What time people walk past, weekdays and weekends apart.
- **By month and by year.** Totals over time, and a table for the front of the paper.
- **North against south.** Whether the two sides of the crossing are balanced.
- **Event days.** A list of days that are nothing like a normal day, such as Grand Final Friday, Melbourne Cup and Christmas. Dana started keeping it because those days kept being read as real changes in how many people walk to work.
- **A csv download**, so people stop asking for the numbers by email.

## How to run it

    python3 -m footfall.server

Then open http://localhost:8000. To check nothing is broken:

    python3 check.py

It renders every page and fails loudly if one of them throws.

## Its condition

It works. The smoke check passes. It has never had a second maintainer.
`,
}

/**
 * The same brief as a printed page.
 *
 * The website wraps the text above in chrome: a heading, a lede naming the
 * project, and a callout saying the requests will tell you what to change. A
 * printed page has no chrome, so the two sentences a reader needs before they
 * start reading go in the text. They used to be typed a second time by hand in
 * docs/study/materials/, which is how the printed welcome came to promise a
 * different session length than the website did.
 *
 * The note goes above the brief rather than after its first paragraph, where the
 * hand-written file had it. Knowing the page is untimed is worth more before
 * someone starts reading it than a paragraph in.
 */
export const BRIEF_UNTIMED =
  'Nothing is timed on this page. Read it at whatever pace you want and ask anything you like ' +
  'before you continue.'

export const BRIEF_NO_MEMORISE =
  'The requests tell you what needs to change, and this is here so that finding your way around ' +
  'is not the first thing you have to do under a clock.'

export function sheetBriefMd(project: Project): string {
  const note = `${BRIEF_UNTIMED} You do not have to memorise any of it. ${BRIEF_NO_MEMORISE}`
  return `# The project: ${project}\n\n${note}\n\n${PROJECT_BRIEF[project]}\n`
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
- **Commit Graph.** The GitLens icon in the left bar, or *GitLens: Show Commit Graph* from the command palette. The history as a graph you can click through.
- **File History.** Right-click any file, *Open File History*. Blame also appears greyed out at the end of whichever line your cursor is on.

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

Makes a new commit that undoes an old one. It can conflict if later commits touched the same lines. When it does, git stops and leaves the conflict markers in the file for you to resolve, then \`git add\` the file and \`git revert --continue\`. \`git revert --abort\` walks away from the whole thing.

To put back something you reverted, revert the revert:

\`\`\`
git revert HEAD
\`\`\`

Or throw the lot away with \`git reset --hard 7e6e383\`. This is the practice copy, so break it if you like.

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
## What it is

\`sgt\` sits on top of an ordinary git repository. Git records which lines in which files changed. \`sgt\` records which functions and classes changed, and groups related work under a name.

Two words are worth learning, because you will type both of them.

A **feature** is a body of work that grew over time, like "the hourly charts".

A **chapter** is one step inside a feature, like "split it into weekday and weekend". Chapters are what you usually want. A feature can be months of work; a chapter is normally one afternoon.

Ten minutes will not make you fluent and we do not expect it to. Every command ends by printing what you might want to run next.

## The practice project

Run \`study-practice\`. It puts you in a throwaway copy of a small shopping cart program. Nothing you do to it counts.

If anything below shows names you do not recognise, you are in the real project. Run \`study-practice\` again.

## 1. The editor first

    study-code

That opens the practice project in VS Code with the **semi-git** extension. Click the semi-git icon in the left bar:

- **Now**, for where things stand.
- **Features**, the work as a tree. Expand a feature to see its chapters.
- **Changes**, for what you have edited and not yet saved.

At the bottom, the **workbench** panel draws every feature as a row across time. The chips under each row are its chapters.

Right-clicking a feature or a chapter offers the same verbs as the commands below. **Toggle Feature Blame** puts the owning feature at the end of whichever line your cursor is on.

## 2. Look around, in the terminal

    sgt now           where things stand
    sgt log           the jobs somebody did, newest first, in their own words
    sgt log --map     one row per feature, with its chapters underneath

In \`--map\`, the bars show how busy a feature was at that moment. The \`@0\`, \`@1\`, \`@2\` chips underneath are its chapters, each with a name.

## 3. List the chapters

    sgt intent list

One line per chapter, each with the handle you can type back:

    ● The Cart  [f-3f9a21b4]  3 checkpoint(s)
        [0] Cart Basics        (f-3f9a21b4@0)
        [1] Remove Items       (f-3f9a21b4@1)
        [2] Cart Total         (f-3f9a21b4@2)

## 4. Ask what one thing is

Hand back a handle, a name, or a function:

    sgt show "The Cart@Cart Total"       what that chapter covers
    sgt show cart.py::total              what one function belongs to

The chapter view tells you which symbols it covers, which saves built it, and what removing it would cost. Read that last line before you remove anything.

## 5. Find something when you do not know its name

Describe it:

    sgt find "the bit that works out postage"

It ranks features, chapters and functions against your words. The search box in the workbench toolbar does the same.

## 6. Take one chapter out, and put it back

Do this whole sequence. It is the most useful thing in these ten minutes.

    sgt revert "The Cart@Cart Total"

Nothing has happened yet. That was a preview, and three things in it are worth reading: which chapter is marked **removed**, which ones say **kept**, and the line saying how many other features are unchanged. Now do it:

    sgt revert "The Cart@Cart Total" --yes
    python -m pytest -q

Then put it back:

    sgt undo
    python -m pytest -q

\`sgt undo\` reverses whatever you last did, and it is the one to reach for. There is also \`sgt restore "The Cart@Cart Total"\`, which takes the same words as \`revert\`, but it does not always bring everything back, so check the result if you use it.

You can name the whole feature instead of one chapter, and it will take the lot. The preview lists every chapter it would remove, so read it before saying yes.

## 7. Your assistant

\`claude\` starts it in the study shell. It can drive this tool as well as the shell, so "what happened to the free shipping rule" and "take the cart total out" are both things you can type at it.

It can also plan before it acts. Ask it to plan first, or use its plan mode, and it lays out the steps before touching anything. You do not have to try that now.

## 8. Help

    sgt --help
    sgt <command> --help

## Before we start

Tell us if anything printed something you could not make sense of.
`

export function tutorialFor(condition: Condition): string {
  return condition === 'git' ? TUTORIAL_GIT : TUTORIAL_SGT
}

/**
 * What the practice step says before the sheet itself, on both surfaces.
 *
 * It used to be the first line of each tutorial body and a differently worded
 * paragraph in Tutorial.tsx, so the website showed a participant the same
 * instruction twice, in two wordings, one directly under the other.
 */
export const TUTORIAL_LEDE =
  'Ten minutes on a practice project first. Ask anything now. Once the real requests start we ' +
  'can only answer questions about the requests themselves.'

/**
 * The same practice sheet as a printed page. See `sheetBriefMd` for why the
 * printed pages are generated rather than typed again.
 *
 * The facilitator hands this over at the practice step and a participant keeps it
 * next to the keyboard through the requests, so the commands on it have to be the
 * commands the website taught, character for character. The hand-written copy of
 * this sheet quoted `sgt revert "Shipping"@2` while the website said `sgt revert
 * Shipping@2`, which fails on any feature name with a space in it.
 */
export function sheetTutorialMd(condition: Condition): string {
  return `# Practice: ${condition}\n\n${TUTORIAL_LEDE}\n\n${tutorialFor(condition)}\n`
}

/** Spells the small counts the prose below quotes. `3 requests` in a sentence reads
 * like a field in a form. Only the numbers this file can produce are covered. */
export const spell = (n: number) =>
  ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'][n] ?? String(n)

/**
 * What the participant is told before the first card of a block.
 *
 * The counts and the total come from the request list. Written down, they went
 * stale the moment the two reach trials were added: this said "three requests,
 * about twenty minutes in total" while the block was five cards and 28 minutes,
 * and a participant who had read that met two cards they were never told about.
 */
export const TASK_PREAMBLE = (app: string, maintainer: string, blurb: string) =>
  `
You have taken over **${app}**, the dashboard you just read about, ${blurb}. ${maintainer} built it over the last six weeks, mostly by describing what she wanted to an AI assistant, and has now left the team.

You have the code, its full history, and your assistant. There are ${spell(CARD_COUNT)} cards to work through in order, ${BLOCK_CAP_MIN} minutes of work in total. Some cards tell you exactly what to run. The ones that matter leave it entirely to you, and those are marked. Every card has its own clock, and running out of time on one is a normal result rather than a failure.

Tell us what you are thinking as you go.
`.trim()

/**
 * The requests as a printed page, for the participant to keep beside the keyboard.
 *
 * Built from the same request list the cards render, because this is the sheet
 * where drift does real damage: the multiple-choice options are the answer the
 * participant gives, so a sheet listing four options against a screen listing five
 * makes a recorded answer mean nothing. The hand-written copy had already gone
 * stale on the preamble, promising three requests and twenty minutes against a
 * block of five cards and 28 minutes.
 *
 * The step carrying the reach prediction IS printed -- it is the step that
 * reverses the work, and leaving it off took the middle out of the sheet, so the
 * paper went from "find what caused it" straight to putting it back. What is left
 * off is the grid of checkboxes inside it: answered twice against two
 * clocks, it cannot be represented on paper without inviting someone to fill it
 * in there, so the sheet names it and points at the screen.
 */
export function sheetTasksMd(project: Project): string {
  const { app, maintainer, blurb } = SCENARIO[project]
  const out = [
    '# Your tasks',
    '',
    TASK_PREAMBLE(app, maintainer, blurb),
    '',
    'Where a step asks you to tick things, the list is on screen and not on this sheet.',
  ]

  for (const card of taskCards(project)) {
    const printed = card.requests

    out.push('', `## ${card.heading}: ${card.title}`, '')
    out.push(
      printed.length > 1
        ? `You have ${card.capMin} minutes for ${card.heading.toLowerCase()} together.`
        : `You have ${card.capMin} minutes.`,
    )

    for (const r of printed) {
      // Only a shared card headings its requests separately; on a card of one the
      // card heading already covers it. Numbered, because request 3 opens with
      // "One correction to the last request".
      if (printed.length > 1) out.push('', `### ${requestHeading(r)}: ${r.title[project]}`)
      out.push('', r.body[project])
      // A prescribed step prints the command AND what it runs. The point of
      // prescribing it is that both arms see identical output; the point of
      // printing what it does is that neither arm has to take that on trust.
      if (r.reach) {
        out.push('', `**Before you change anything:** ${r.reach.work[project]}`)
        out.push(
          '',
          `On screen there is a list of the things you can see on ${SCENARIO[project].app}'s pages. ` +
            `Tick the ones you think this will affect. ${r.reach.blindSec} seconds, then it ` +
            'submits itself. You answer once more afterwards, knowing what happened.',
        )
      }
      if (r.run) {
        out.push('', '```', r.run.script[project], '```')
        out.push('', 'It:', '')
        out.push(...r.run.does[project].map((d) => `- ${d}`))
      }
      if (r.identify) out.push('', `**${r.identify[project]}:** ______________________`)
      if (r.note) out.push('', `**${r.note[project]}**`)
    }
  }
  return out.join('\n') + '\n'
}

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

One of the two setups you used was that tool. The other was ordinary git. We deliberately did not say which was which, and we asked the same ${spell(CARD_COUNT)} cards of both, because we are measuring the difference between the two representations and not the difference between you and anyone else.

## What happens to your data

Your recordings, your answers, and the log of commands and prompts from your machine are stored under a participant code, not your name. Your name appears only on the consent record, which is kept separately. Results are reported in aggregate. If you ticked the optional consent line, a short quote might appear, with nothing in it that identifies you.

If you want your data removed, email us with your participant code and we will delete it. No reason needed.
`.trim()
