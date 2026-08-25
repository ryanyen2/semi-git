// Participant-facing prose: the welcome, the practice sheets, the task-block
// preamble, the interview page, the handover, and the debrief.
//
// One wording, not two: the printed sheets in docs/study/materials/ are
// generated from this file (`npm run gen:materials`), and a test fails if a
// file on disk has drifted.

import type { Condition, Project } from '../lib/types'
import {
  BLOCK_ESTIMATE_MIN,
  REQUESTS,
  SCENARIO,
  STAGE_COUNT,
  requestHeading,
} from './tasks'
import { TOTAL_ESTIMATE_MIN } from './flow'

// The numbers in the schedule below are read from the step list and the stage
// caps rather than written out. The old copy said 110 minutes while the steps
// added up to 113, which is the harmless-looking version of a promise the
// session cannot keep.
//
// The figure at the top is rounder and more generous than the sum: it is what
// someone blocks out in a calendar, and it has to hold with breaks, a slow
// install, and a facilitator explaining something twice. A test keeps it above
// the computed total, so adding a stage cannot quietly overrun the promise the
// participant agreed to.
export const PLAN_FOR = 'an hour and a half'

export const WELCOME_MD = `
Thanks for taking part. Plan for ${PLAN_FOR}, including breaks.

## What you'll do

- Work through four short stages on a small web dashboard: record a change an AI assistant made, find the piece of work behind a wrong number, take that work out, and put it back.
- Do that twice, with two different setups for reading and changing the project's history, on two different projects.
- At the end, look at one of your own repositories through one of the setups, and talk with us about it.

Each stage tells you exactly what has happened and what to do. You never have to work out what the project is for, and a script resets the project between stages, so nothing you do in one stage can spoil the next.

We are comparing the two setups. We are not testing you. If something confuses you, that is the most useful thing you can tell us.

## Please think out loud

- Say what you are about to do, and what you expect to happen.
- When something surprises you, say what you expected instead.
- When you don't understand what you are looking at, say so.

We record the screen and audio. You can ask us to stop at any time.

## One thing that may surprise you

There is no AI assistant to chat with during the timed stages. The first stage shows you changes an assistant made earlier; you read them and record them, but you do not direct it. We removed the live assistant so that every participant sees exactly the same changes.

## How the time goes

| Minutes | What |
|---|---|
| 4 | Consent and a few questions about your background |
| 6 | Setting up your machine |
| 5 | Practice with the first setup, on a throwaway project |
| ${BLOCK_ESTIMATE_MIN} | Four stages with the first setup, each a few minutes plus a few questions |
| 2 | Questions about that setup |
| 6 | Setting up and practising the second setup |
| ${BLOCK_ESTIMATE_MIN} | The same four stages with the second setup, on the second project |
| 2 | Questions again |
| 3 | Comparing the two |
| 15 | One of your own repositories, and a short interview |
| 2 | Handing your data over |

That is about ${TOTAL_ESTIMATE_MIN} minutes of work. The rest is breaks.

Each stage has a visible countdown for the doing part. The questions after a stage are never timed. We expect people to run out of time on some stages. That is a normal result, not a problem.

## One thing to know

Nothing you do can break anything that matters. Every project is a fresh copy, and every stage starts by resetting it. If you get stuck, say so. Getting stuck is information for us, so please don't hide it.

## Your own machine stays untouched

The setup step installs everything inside one folder and uses its own Python. It does not change your shell, your global packages, or anything you have installed. If you brought a repository of your own for the interview, it is only read, never changed.
`.trim()

/**
 * The same welcome, as the printed handout in `docs/study/materials/00-welcome.md`.
 *
 * That file used to be written by hand alongside this one, and the two drifted
 * until they promised different session lengths. `npm run gen:materials` writes
 * the file from here, and a test fails if it drifts.
 *
 * The only difference is the title: the website renders its own, a printed page
 * needs one in the text.
 */
export const HANDOUT_MD = `# Welcome\n\n${WELCOME_MD}\n`

// ---------------------------------------------------------------------------
// The two practice sheets
// ---------------------------------------------------------------------------
//
// Both are written editor-first. Pilots of the old design read a sheet made
// entirely of terminal commands, then met the tasks inside an editor they had
// been given but never shown, and several never opened the history view at
// all, which turns "does this representation help" into "did you find the
// panel".
//
// Each sheet teaches exactly the four actions the stages need: read a change
// in the history view, record work, find a piece of work, and take one out
// and put it back. Nothing else. Every command, id, and name quoted below is
// real in the warm-up repository that scripts/make-practice-repo.sh builds,
// and that script re-checks each of them at the end of a build.

const TUTORIAL_GIT = `
You already know git. This is not a lesson. It is a warm-up on the four things the stages will ask you to do, so that nothing on this machine surprises you later.

## The practice project

Run \`study-practice\`. It puts you in a throwaway copy of a small shopping cart program. Nothing you do to it counts.

It has four pieces: \`cart.py\` (adding and removing things, and the total), \`discount.py\` (a percentage off, or a coupon code), \`receipt.py\` (printing a receipt), and \`shipping.py\` (what postage costs). Sixteen commits, and \`python -m pytest -q\` passes.

If \`ls\` shows anything else, you are in the real project. Run \`study-practice\` and try again.

## 1. Open the editor

\`\`\`
study-code
\`\`\`

That opens the practice project in VS Code with **GitLens** installed. Find these now, because the stages will want them:

- **Source Control** in the left bar, for what has changed and where you commit.
- **Commit Graph.** The GitLens icon in the left bar, or *GitLens: Show Commit Graph* from the command palette. The history as a graph you can click through.
- **File History.** Right-click any file, *Open File History*.

## 2. Read one change

Open the Commit Graph and click a commit. You see what it changed, file by file. The same thing in the terminal:

\`\`\`
git show 44da4ad
\`\`\`

## 3. Record some work

Make a small edit to \`receipt.py\` (change any wording in a string). Then record it the way you normally would: stage it and commit it in Source Control, with a message. Or in the terminal:

\`\`\`
git add receipt.py
git commit -m "reword the receipt footer"
\`\`\`

Stage 1 asks you to do exactly this, on changes someone else made.

## 4. Find a piece of work

\`git log -S\` finds the commits where some text arrived or went away:

\`\`\`
git log --oneline -S "FREE_OVER"
\`\`\`

Three commits come back: free shipping over fifty arrived, then vanished inside a commit about per-item pricing whose message does not mention it, then came back. Try to see the same story in the Commit Graph. Also useful: \`git log --stat\`, \`git blame shipping.py\`, and File History in the editor.

## 5. Take something out, and put it back

\`\`\`
git revert 7e6e383
\`\`\`

That makes a new commit undoing an old one. It can conflict if later commits touched the same lines. When it does, git stops and leaves conflict markers in the file: resolve them, \`git add\` the file, and \`git revert --continue\`. Or walk away with \`git revert --abort\`.

To put back what you removed, revert the revert:

\`\`\`
git revert HEAD
\`\`\`

Do this whole loop once now, conflicts and all. It is the most useful thing on this sheet, and this is the free copy.

## Before we start

Tell us if any of that behaved differently from what you expected.
`.trim()

const TUTORIAL_SGT = `
## What it is

\`sgt\` sits on top of an ordinary git repository. Git records which lines in which files changed. \`sgt\` records which functions and classes changed, and groups related work under a name.

Two words are worth learning, because you will type both.

A **feature** is a body of work that grew over time, like "the hourly charts".

A **chapter** is one step inside a feature, like "split it into weekday and weekend". Chapters are what you usually want: a feature can be months of work, a chapter is normally one afternoon.

Ten minutes will not make you fluent and we do not expect it to. Every command ends by printing what you might want to run next.

## The practice project

Run \`study-practice\`. It puts you in a throwaway copy of a small shopping cart program. Nothing you do to it counts. If anything below shows names you do not recognise, you are in the real project. Run \`study-practice\` again.

## 1. Open the editor

    study-code

That opens the practice project in VS Code with the **semi-git** extension. Click the semi-git icon in the left bar:

- **Now**, for where things stand.
- **Features**, the work as a tree. Expand a feature to see its chapters.
- **Changes**, for what you have edited and not yet saved.

At the bottom, the **workbench** panel draws every feature as a row across time. The chips under each row are its chapters. Right-clicking a feature or a chapter offers the same verbs as the commands below.

## 2. Read one change

Click a chapter in the Features tree or the workbench. It shows what the chapter covers, in functions rather than lines. The same thing in the terminal:

    sgt show "The Cart@Cart Total"

\`sgt log\` lists the jobs somebody did, newest first, in their own words, and \`sgt log --map\` draws one row per feature.

## 3. Record some work

Make a small edit to \`receipt.py\` (change any wording in a string). The **Changes** view shows it. Record it:

    sgt save

It describes what you changed and files it under the feature it belongs to. Read what it printed: that wording is the record. Stage 1 asks you to do exactly this, on changes someone else made.

## 4. Find a piece of work

Describe it in your own words:

    sgt find "the bit that works out postage"

It ranks features, chapters and functions against your words. The search box in the workbench toolbar does the same. \`sgt intent list\` lists every chapter with a handle you can type back.

## 5. Take something out, and put it back

Do this whole sequence now. It is the most useful thing on this sheet.

    sgt revert "The Cart@Cart Total"

Nothing has happened yet. That was a preview. Three things in it are worth reading: which chapter is marked **removed**, which say **kept**, and the line saying how many other features are unchanged. Now do it:

    sgt revert "The Cart@Cart Total" --yes
    python -m pytest -q

Then put it back:

    sgt restore "The Cart@Cart Total"
    python -m pytest -q

\`restore\` is \`revert\`'s opposite and takes the same words. If you ever lose track of where you are, \`sgt undo\` reverses whatever you last did, and \`sgt now\` says where things stand.

## 6. Help

    sgt --help
    sgt <command> --help

## Before we start

Tell us if anything printed something you could not make sense of.
`.trim()

export function tutorialFor(condition: Condition): string {
  return condition === 'git' ? TUTORIAL_GIT : TUTORIAL_SGT
}

/**
 * What the practice step says before the sheet itself, on both surfaces.
 */
export const TUTORIAL_LEDE =
  'A few minutes on a practice project first. Ask anything now. Once the stages start we ' +
  'can only answer questions about the stage instructions themselves.'

/**
 * The same practice sheet as a printed page. Generated rather than typed
 * again, because the hand-written copy of this sheet once quoted a command
 * that fails on any feature name with a space in it while the website showed
 * the working one.
 */
export function sheetTutorialMd(condition: Condition): string {
  return `# Practice: ${condition}\n\n${TUTORIAL_LEDE}\n\n${tutorialFor(condition)}\n`
}

/** Spells the small counts the prose below quotes. `4 stages` in a sentence
 * reads like a field in a form. */
export const spell = (n: number) =>
  ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'][n] ?? String(n)

/**
 * What the participant is told at the top of a task block.
 *
 * The counts come from the stage list. Written down, they went stale through
 * two redesigns of what was under them.
 */
export const TASK_PREAMBLE = (app: string, maintainer: string, blurb: string) =>
  `
You are looking after **${app}**, ${blurb}. ${maintainer} built it over six weeks and has left the team. It reads a public csv of hourly sensor counts and renders a handful of pages: a front page, an hour-of-day page, monthly and yearly totals, a comparison of the two sensors, and a csv download. Its numbers go into a quarterly report.

There are ${spell(STAGE_COUNT)} stages, in order. Each one starts with a script that puts the project in that stage's starting state, tells you what has happened, and asks you to do one thing. The doing part has a visible countdown. The questions after it do not.

Running out of time on a stage is a normal result, and the next stage starts clean either way. Tell us what you are thinking as you go.
`.trim()

/**
 * The stages as a printed page, for the participant to keep beside the
 * keyboard. Built from the same stage list the cards render, because this is
 * the sheet where drift does real damage.
 *
 * The checklists and rating scales are not printed: they are answered on
 * screen after each stage, and a paper copy would invite someone to fill them
 * in early. The sheet names them and points at the screen.
 */
export function sheetTasksMd(project: Project): string {
  const { app, maintainer, blurb } = SCENARIO[project]
  const out = [
    '# Your stages',
    '',
    TASK_PREAMBLE(app, maintainer, blurb),
    '',
    'After each stage, the screen asks a few short questions about what you just did. They are not timed.',
  ]

  for (const r of REQUESTS) {
    out.push('', `## ${requestHeading(r)}: ${r.title[project]}`, '')
    out.push(`You have ${r.capMin} minutes for the doing part.`)
    out.push('', r.body[project])
    out.push('', `What \`${r.run.script[project]}\` does:`, '')
    out.push(...r.run.does[project].map((d) => `- ${d}`))
    if (r.identify) out.push('', `**${r.identify[project]}:** ______________________`)
  }
  return out.join('\n') + '\n'
}

// ---------------------------------------------------------------------------
// The interview page
// ---------------------------------------------------------------------------

/**
 * Shown on the own-repository step. The interview itself is the
 * facilitator's; this page tells the participant what is about to happen and
 * what was, or was not, done with their repository.
 */
export const INTERVIEW_MD = `
For the last part, we step away from our projects and look at one of yours.

If you brought a repository and ticked the consent line for it, we started building its history view during setup, and your facilitator will now open it. If you did not, we use a prepared public repository instead. Either way, nothing is changed, and nothing from a repository of yours is kept after the session except the recorded conversation.

There are no tasks and no clocks here. We will ask you to walk through what you see, say where it matches how you think about your own work, and say where it is wrong. The places where it is wrong are the most useful thing you can give us.
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

That removes the study folders, the session's keys, the editor profile it made, and the history view built for the interview, from your machine. The projects get reused with other participants, so please do run it.

Nothing was installed outside the study folder, and your own repository, if you brought one, was only read.
`.trim()

export const DEBRIEF_MD = `
That is everything. Thank you.

## What this was about

Version control records history as changed lines in files. More and more of the code in a repository is written by AI assistants, in units of "what I asked for" rather than lines. We built a tool that records history as the pieces of work someone meant to do, and we wanted to know whether that helps at three specific moments: when you record work an assistant did, when you look for the work behind a defect, and when you take a piece of work out or put it back.

One of the two setups you used was that tool. The other was ordinary git. We deliberately did not say which was which, and we asked the same ${spell(STAGE_COUNT)} stages of both, because we are measuring the difference between the two representations, not the difference between you and anyone else.

The changes you recorded in stage 1 really were made by an AI assistant, but earlier, in a recorded session that a script replayed onto your machine. Every participant read exactly the same changes.

## What happens to your data

Your recordings, your answers, and the log of commands from your machine are stored under a participant code, not your name. Your name appears only on the consent record, which is kept separately. Results are reported in aggregate. If you ticked the optional consent line, a short quote might appear, with nothing in it that identifies you.

If you want your data removed, email us with your participant code and we will delete it. No reason needed.
`.trim()
