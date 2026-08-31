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
Thanks for taking part. Plan for ${PLAN_FOR}, including breaks. Your facilitator walks you through every step, so you never have to work out on your own what comes next.

## What you'll do

- Work through four short stages on a small web dashboard: record a change an AI assistant made, find the piece of work behind a wrong number, take that work out, and put it back.
- Do that twice, with two different setups for reading and changing the project's history, on two different projects.
- At the end, look at one of your own repositories through one of the setups, and talk with us about it.

We are comparing the two setups, not testing you. If something confuses you, saying so is the most useful thing you can do for us.

## Think out loud

Say what you are about to do and what you expect to happen, and say when something surprises you. We record the screen and audio, and you can ask us to stop at any time.

## The schedule

| Minutes | What |
|---|---|
| 4 | Consent and a few questions about your background |
| 6 | Setting up your machine |
| 5 | Practice with the first setup, on the project itself |
| ${BLOCK_ESTIMATE_MIN} | Four stages with the first setup, each a few minutes plus a few questions |
| 2 | Questions about that setup |
| 6 | Setting up and practising the second setup |
| ${BLOCK_ESTIMATE_MIN} | The same four stages with the second setup, on the second project |
| 2 | Questions again |
| 3 | Comparing the two |
| 15 | One of your own repositories, and a short interview |
| 2 | Handing your data over |

That is about ${TOTAL_ESTIMATE_MIN} minutes of work. The rest is breaks. Each stage has a visible countdown for the doing part, the questions after a stage are untimed, and running out of time on a stage is a normal result.

## Good to know

- There is no AI assistant to chat with during the timed stages. The first stage shows you changes an assistant made earlier, and every participant sees exactly the same ones.
- Nothing you do can break anything. Every project is a fresh copy, and every stage starts by resetting it. If you get stuck, say so rather than hiding it.
- The setup installs everything inside one folder with its own Python. Your shell, your global packages, and anything you have installed stay untouched, and a repository of your own is only read, never changed.
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
// The warm-up used to happen on a throwaway shopping-cart repository built by
// scripts/make-practice-repo.sh. It is gone, and the practice now happens on
// the project the stages use, for two reasons.
//
// The first is that the sheets quoted ids from that repository -- `git show
// 44da4ad`, `sgt show "The Cart@2"` -- and a participant who typed them got
// `unknown revision`, because the id was real in a repository they were no
// longer standing in. Nothing here quotes an id any more. Every command below
// either needs no id, or says to take one from what the previous command
// printed, which is what somebody does in their own work anyway.
//
// The second is that ten minutes spent learning a shopping cart is ten minutes
// not spent learning the dashboard four timed stages are about. Practising on
// the real project means the codebase tour and the tool warm-up are the same
// ten minutes. `./stage 0` puts the project back to just before the assistant's
// changes, so the warm-up cannot show them the work stage 1 asks them to read.
//
// Both sheets are written editor-first. Pilots of the old design read a sheet
// made entirely of terminal commands, then met the tasks inside an editor they
// had been given but never shown, and several never opened the history view at
// all, which turns "does this representation help" into "did you find the
// panel".
//
// Each sheet teaches exactly the four actions the stages need: read a change,
// record work, find a piece of work, and take one out and put it back.
//
// The git sheet's conflict section is deliberately concrete. A pilot who hit
// the practice revert got git's own hint ("git add/rm <pathspec>"), typed it
// literally with a commit hash, then resolved the modify/delete conflict the
// wrong way round -- kept the page file, removed the function it calls -- and
// check.py failed with an AttributeError they had no way to interpret. The
// sheet now walks through both conflict kinds and says which resolution keeps
// the project running. The same pilot was trapped twice by the terminal
// itself: less on `git log`, pico on `git revert HEAD`. Those are fixed in the
// bundle (core.pager and core.editor, set by install/setup.sh), and the sheet
// says so where a git user would expect the old behavior.
//
// The prose style follows the pilot feedback too: complete sentences, plain
// words, and no sentence that names a thing the participant cannot see.

/** The project itself, in both conditions. The stages are about this codebase,
 * so the warm-up is the only place anybody gets told how it is put together. */
const PROJECT_TOUR: Record<Project, string> = {
  bikecount: `
## The project you are looking after

**bikecount** is a small web dashboard over the bicycle counter on the Fremont Bridge in Seattle. The city has counted people crossing every hour since 2013 and publishes the file. The dashboard reads that file and draws a handful of pages, and its numbers go into the cycling team's quarterly report.

Start it and look at it:

    python3 -m bikecount.server

Then open http://localhost:8000 and click through the five pages: the front page, the hour-of-day page, monthly totals, the by-year table, and the east against west comparison. There is also a csv download at \`/daily.csv\`. Stop the server with Ctrl-C when you have seen them.

The code is laid out like this:

- \`bikecount/pages/\` is one file per page. Each one has a \`render()\` that returns the html for that page, and the navigation and the routing are built from whatever is in that folder.
- \`bikecount/metrics.py\` works out the numbers the pages show: daily totals, the busiest day, the hour-of-day averages, the by-year summary.
- \`bikecount/data.py\` reads \`data/counts.csv\` and hands the rows to everything else.
- \`bikecount/charts.py\` draws the bar charts the pages embed.
- \`check.py\` renders every page and fails if one of them blows up. It is the quickest way to see whether the project still works, and it takes about a second:

\`\`\`
python3 check.py
\`\`\`

You will not have to write any code in the stages. You will have to read some.
`,
  footfall: `
## The project you are looking after

**footfall** is a small web dashboard over the pedestrian counter on Spencer Street in Melbourne. The city has counted people walking past every hour since 2013 and publishes the file. The dashboard reads that file and draws a handful of pages, and its numbers go into the transport committee's quarterly paper.

Start it and look at it:

    python3 -m footfall.server

Then open http://localhost:8000 and click through the five pages: the front page, the hour-of-day page, monthly totals, the by-year table, and the north against south comparison. There is also a csv download at \`/daily.csv\`. Stop the server with Ctrl-C when you have seen them.

The code is laid out like this:

- \`footfall/pages/\` is one file per page. Each one has a \`render()\` that returns the html for that page, and the navigation and the routing are built from whatever is in that folder.
- \`footfall/metrics.py\` works out the numbers the pages show: daily totals, the busiest day, the hour-of-day averages, the by-year summary.
- \`footfall/data.py\` reads \`data/counts.csv\` and hands the rows to everything else.
- \`footfall/charts.py\` draws the bar charts the pages embed.
- \`check.py\` renders every page and fails if one of them blows up. It is the quickest way to see whether the project still works, and it takes about a second:

\`\`\`
python3 check.py
\`\`\`

You will not have to write any code in the stages. You will have to read some.
`,
}

/** Where the warm-up starts, in both conditions. */
const WARM_UP_STATE = `
## Put the project in its warm-up state

In the session shell, run:

\`\`\`
./stage 0
\`\`\`

The command puts the project back to where it stood just before the changes the first stage is about, so nothing you see during the warm-up spoils that stage. Running \`./stage 1\` later undoes everything you do between now and then, so nothing you try here can go wrong.
`

const GIT_OPENING = `
You already know git, so this page is not a lesson. It is a warm-up on the four things the stages will ask you to do, on the same project the stages use. The point is that nothing about this machine should surprise you once a stage's clock is running.
`

const GIT_BODY = `
## Open the editor

\`\`\`
study-code
\`\`\`

The command opens the project in VS Code. Find these three places now, because the stages use all of them:

- **Source Control**, in the left bar, lists what you have changed and is where you commit.
- **Graph**, in the lower half of that same view, draws the history as a graph you can click through.
- **Timeline**, at the bottom of the Explorer, lists the commits that touched the file you click.

## 1. Read one change

Open the Graph and click a commit. The editor shows what that commit changed, file by file. The terminal shows the same thing:

\`\`\`
git log --oneline
git show <paste a hash from that list>
\`\`\`

On this machine git prints straight to the terminal. It never opens the pager screen that you would normally leave by pressing q, so long output simply scrolls past and you scroll back to read it.

Read the commit that added the csv download, and the one that added the by-year table. Together they show most of how the pages are put together.

## 2. Record some work

Open \`README.md\` and change a word in it. Then record the change the way you normally would. Stage it and commit it in Source Control with a message, or do the same in the terminal:

\`\`\`
git add README.md
git commit -m "reword a line in the readme"
\`\`\`

Stage 1 asks you to do exactly this, on changes somebody else made.

## 3. Find a piece of work

\`git log -S\` finds the commits where a piece of text arrived or went away. Any word from the code works:

\`\`\`
git log --oneline -S "average"
\`\`\`

The Graph has no search like that, so use the two together. The command gives you the hashes, and clicking those commits in the Graph shows what each one changed.

Two more commands are useful here. \`git log --stat\` lists the files each commit touched, and \`git blame <file>\` names the commit that last changed each line. The editor shows the same information in two places: the Timeline lists the commits that touched the open file, and the grey note at the end of the line your cursor is on names the commit that last changed that line.

Stage 2 asks you to find one particular piece of work this way.

## 4. Take something out, and put it back

\`\`\`
git revert HEAD
\`\`\`

The command makes a new commit that undoes the most recent one. On this machine git uses the commit message it suggests instead of opening a terminal editor, so the revert finishes in one step. Put the work back by running \`git revert HEAD\` again, which reverts the revert.

**Now try one that does not apply cleanly.** In \`git log --oneline\`, find the commit that first added the hour-of-day page, and revert it:

\`\`\`
git revert <that hash>
\`\`\`

Git stops partway, because later commits built on the work you are removing. It leaves the affected files for you to settle, and \`git status\` lists them under "Unmerged paths". You will see two kinds:

- A file listed as **both modified** has conflict markers in it, from \`<<<<<<<\` to \`>>>>>>>\`. Open the file in the editor and edit it down to the lines that should survive, with the marker lines deleted. Save it, then run \`git add <that file>\`.
- A file listed as **deleted by them** has no markers. The revert wants to delete the whole file, but a later commit changed it, so git leaves the choice to you. Run \`git rm <that file>\` to delete it, or \`git add <that file>\` to keep it. Here the right choice is \`git rm\`, because the page is part of the work you are removing. A kept page file still calls the functions the revert takes out, and the project will not run.

Git's own hint writes those two commands as \`git add/rm <pathspec>\`. It is shorthand for "\`git add\` or \`git rm\`, followed by a file path", not a command to type as written.

When \`git status\` no longer lists unmerged paths, finish the revert and check the project:

\`\`\`
git revert --continue
python3 check.py
\`\`\`

If the check fails, read which file the error names. The usual cause is a kept file that still calls something the revert removed. If you want out instead, any time before the \`--continue\`, run \`git revert --abort\` and everything goes back to how it was before the revert.

Do this now. Stage 3 asks you to remove work that several later commits have landed on, and the warm-up is the only place to practise getting out of a conflicted revert.

## Before we start

Run \`python3 check.py\` once more to see that the project still works, and tell us if anything above behaved differently from what you expected.
`

const SGT_OPENING = `
## What it is

\`sgt\` sits on top of an ordinary git repository. Git records which lines in which files changed. \`sgt\` records which functions and classes changed, and groups related work under a name.

Two words are worth learning, because you will type both.

A **feature** is a body of work that grew over time, such as "the hourly charts".

A **checkpoint** is one step inside a feature, such as "split it into weekday and weekend". Checkpoints are usually what you want, because a feature can be months of work while a checkpoint is normally one afternoon. Some screens call checkpoints **chapters**. They are the same thing.

Ten minutes will not make you fluent, and we do not expect it to. Every command ends by printing what you might want to run next.
`

const SGT_BODY = `
## Open the editor

    study-code

The command opens the project in VS Code with the **semi-git** extension. Click the semi-git icon in the left bar to find three views:

- **Now** says where things stand.
- **Features** shows the work as a tree. Expand a feature to see its checkpoints.
- **Changes** lists what you have edited and not yet saved.

At the bottom of the window, the **workbench** panel draws every feature as a row across time, and the chips under each row are its checkpoints. Right-clicking a feature or a chip offers **Revert** and **Restore**, which section 4 below covers.

The sidebar has two more views, **Forks** and **Compositions**. Nothing in this session needs them.

## 1. Read one change

Click a checkpoint in the Features tree or in the workbench. The editor shows what that piece of work covers, in functions rather than lines. The terminal shows the same thing:

    sgt log

The command lists the jobs somebody did, newest first, in their own words. Each row carries a short id, the seven-character code near the left. Pass one of those ids to \`sgt show\`:

    sgt show <a short id from a row>

\`show\` takes an id or an exact name, never a phrase, so pasting a whole row's description back at it will not work. The feature name on the right of a row works too, in quotes:

    sgt show "<a feature name, exactly as it is written>"

Read the work that added the csv download, and the work that added the by-year table. \`sgt log --map\` draws the same history as one row per feature.

## 2. Record some work

Open \`README.md\` and change a word in it. The **Changes** view shows the edit. Record it, in your own words:

    sgt save -m "reword a line in the readme"

The command files your change under the piece of work it belongs to and prints which one. Plain \`sgt save\` works too, and its record then says \`no words captured\`, because only you can say what a change was for. Stage 1 asks you to do exactly this, on changes somebody else made.

## 3. Find a piece of work

Describe the work in your own words:

    sgt find "the bit that works out the averages"

The command lists the closest matches to what you typed, across functions, features, and individual saves. The search box in the workbench toolbar does the same thing.

Some rows are shortened to fit the screen. To get a name you can type back, run:

    sgt intent list

The command prints every feature and checkpoint with its handle. The groups at the bottom are pieces of work that span several features. **The sidebar does not show those groups**, so \`sgt intent list\` is the only place they appear, and stage 3 names one of them.

## 4. Take something out, and put it back

Work through this whole section now, because stages 3 and 4 ask you to do exactly this with a clock running.

Pick a checkpoint from \`sgt intent list\`, one from the middle of the list rather than the newest. Type it by the handle in the brackets, such as \`f-08915a9f@1\`, because a checkpoint's plain name does not resolve on its own. Feature names and the group names at the bottom of the list work as written. Preview taking it out:

    sgt revert "<that handle>"

Nothing has happened yet, because without \`--yes\` the command only prints a preview. Read the preview before going on. It says which checkpoint is **removed** and which are **kept**, a row like **2/6 edits removed** means that checkpoint shares some code with what you are taking out, and a line starting with ⚠ names code that still calls what you are removing. Now do it:

    sgt revert "<that handle>" --yes
    python3 check.py

The check can come out red here. That is the ⚠ line come true: something you kept still calls what you removed, and putting the work back will fix it. Do that now. \`restore\` is \`revert\`'s opposite and takes the same handle, and it needs \`--yes\` for the same reason:

    sgt restore "<that handle>" --yes
    python3 check.py

If you lose track of where you are, \`sgt undo\` reverses whatever you last did, and \`sgt now\` says where things stand.

## 5. Help

    sgt --help
    sgt <command> --help

## Before we start

Run \`python3 check.py\` once more to see that the project still works, and tell us if anything above printed something you could not make sense of.
`

/**
 * One practice sheet: what the tool is, then what the project is, then the four
 * things to try.
 *
 * The project comes second rather than last on purpose. Somebody about to run
 * `sgt find "the bit that works out the averages"` has to know that there are
 * averages, and roughly where. The old sheet described no codebase at all,
 * because the practice happened on an unrelated shopping cart.
 */
export function tutorialFor(condition: Condition, project: Project): string {
  const opening = condition === 'git' ? GIT_OPENING : SGT_OPENING
  const body = condition === 'git' ? GIT_BODY : SGT_BODY
  return (
    [opening.trim(), PROJECT_TOUR[project].trim(), WARM_UP_STATE.trim(), body.trim()].join(
      '\n\n',
    ) + '\n'
  )
}

/**
 * What the practice step says before the sheet itself, on both surfaces.
 */
export const TUTORIAL_LEDE =
  'Take a few minutes on the project itself before the stages start. Ask us anything now, ' +
  'because once the stages start we can only answer questions about the stage instructions themselves.'

/**
 * The same practice sheet as a printed page. Generated rather than typed
 * again, because the hand-written copy of this sheet once quoted a command
 * that fails on any feature name with a space in it while the website showed
 * the working one.
 */
export function sheetTutorialMd(condition: Condition, project: Project): string {
  return `# Practice: ${condition}, ${project}\n\n${TUTORIAL_LEDE}\n\n${tutorialFor(condition, project)}\n`
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
You are looking after **${app}**, ${blurb}. ${maintainer} built it over six weeks and has left the team, and its numbers go into a quarterly report. It is the same project you have just been practising on, and you run everything from the same folder.

There are ${spell(STAGE_COUNT)} stages, in order. Each stage card says what happened, what your job is, and what done looks like, and it lists the commands you are most likely to want, so you do not lose a minute to remembering a flag.

Start each stage by running its \`./stage\` command, which puts the project into that stage's starting state. The clock starts once that command has finished printing, the doing part has a visible countdown, and the questions after it are untimed. Running out of time on a stage is a normal result, and the next stage starts clean either way. Tell us what you are thinking as you go.
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
export function sheetTasksMd(project: Project, condition: Condition): string {
  const { app, maintainer, blurb } = SCENARIO[project]
  const out = [
    `# Your stages: ${project}, ${condition}`,
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
    out.push('', 'Commands you may want:', '')
    out.push(...r.tips[condition].map((t) => `- ${t}`))
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

If you brought a repository and ticked the consent line for it, we built its history view during setup, and your facilitator will open it now. If you did not, we use a prepared public repository instead. Either way nothing is changed, and nothing from a repository of yours is kept after the session except the recorded conversation.

There are no tasks and no clocks here. Walk us through what you see, say where it matches how you think about your own work, and say where it is wrong. The places where it is wrong are the most useful thing you can give us.
`.trim()

export const HANDOVER_MD = `
Almost done. Two steps and you can close everything.

## 1. Send us what your machine recorded

In the study shell, run:

\`\`\`
study-sync --final
\`\`\`

It uploads anything still waiting, and the tick below turns green when we have it. If the tick stays red for more than a minute, tell your facilitator rather than retrying. The log on your disk is safe either way.

## 2. Delete the study folders

Once the tick is green, run:

\`\`\`
study-cleanup
\`\`\`

It removes the study folders, the session's keys, the editor profile, and the history view built for the interview. The projects get reused with other participants, so please do run it.

Nothing was installed outside the study folder, and your own repository, if you brought one, was only read.
`.trim()

export const DEBRIEF_MD = `
That is everything. Thank you.

## What this was about

Version control records history as changed lines in files, but more and more of the code in a repository is written by AI assistants, in units of "what I asked for" rather than lines. We built a tool that records history as the pieces of work someone meant to do, and we wanted to know whether that helps at three moments: when you record work an assistant did, when you look for the work behind a defect, and when you take a piece of work out or put it back.

One of the two setups you used was that tool, and the other was ordinary git. We deliberately did not say which was which, and we asked the same ${spell(STAGE_COUNT)} stages of both, because we are measuring the difference between the two representations, not the difference between you and anyone else.

The changes you recorded in stage 1 really were made by an AI assistant, in an earlier recorded session that a script replayed onto your machine. Every participant read exactly the same changes.

## What happens to your data

Your recordings, your answers, and the log of commands from your machine are stored under a participant code, not your name. Your name appears only on the consent record, which is kept separately, and results are reported in aggregate. If you ticked the optional consent line, a short quote might appear, with nothing in it that identifies you.

If you want your data removed, email us with your participant code and we will delete it. No reason needed.
`.trim()
