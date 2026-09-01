import type { Condition, Project } from '../lib/types'
import {
  BLOCK_ESTIMATE_MIN,
  PROJECT_WORDS,
  REQUESTS,
  SCENARIO,
  STAGE_COUNT,
  requestHeading,
} from './tasks'
import { TOTAL_ESTIMATE_MIN } from './flow'

export const PLAN_FOR = 'an hour and a half'

export const WELCOME_MD = `
Thanks for taking part. Plan for ${PLAN_FOR}, including breaks. Your facilitator will guide you through the session and tell you when to move on.

## What you'll do

You will work through four stages on a small web dashboard:

1. Get to know the project: what work it is made of, and which part of the dashboard each piece of work put there.
2. Find the work that caused a wrong result.
3. Remove that work.
4. Restore it.

You will complete these stages twice. Each round uses a different project and a different way of working with project history.

At the end, we will open a history view for one of your repositories if you chose to provide one. Otherwise, we will use a prepared public repository.

## Think out loud

As you work, say what you are looking for, what you plan to do, and what you expect to happen. Please also say when something is confusing or surprising.

We record your screen and voice during the session.

## Schedule

| Minutes | What you'll do |
|---|---|
| 4 | Consent and background questions |
| 6 | Set up the first project |
| 5 | Practice with the first setup |
| ${BLOCK_ESTIMATE_MIN} | Four stages with the first setup |
| 2 | Questions about the first setup |
| 6 | Set up and practice with the second setup |
| ${BLOCK_ESTIMATE_MIN} | Four stages with the second setup |
| 2 | Questions about the second setup |
| 3 | Compare the two setups |
| 15 | Repository walkthrough and interview |
| 2 | Send the study data and clean up |

The study contains about ${TOTAL_ESTIMATE_MIN} minutes of activities, with breaks during the remaining time.

Each stage has a visible timer for the task itself. The questions after each stage are untimed. When the timer ends, move on to the questions and then continue to the next stage.

## During the stages

- Each stage starts from a prepared project state, so your work in one stage does not affect the next.
`.trim()

export const HANDOUT_MD = `# Welcome\n\n${WELCOME_MD}\n`

const OPEN_EDITOR = `
## Open the editor

In the session shell, run:

\`\`\`
study-code
\`\`\`

Leave the session shell window open in the background for the whole half — it keeps recording the session.

Everything else happens inside the editor. Open a terminal with **Terminal → New Terminal**, then open a second one with the **+** button on the terminal panel.

Use the two terminals like this:

- **Terminal 1** runs the dashboard server, so you can look at the pages.
- **Terminal 2** runs every other command, in this practice and in the stages.
`

const PROJECT_TOUR: Record<Project, string> = {
  bikecount: `
## The project

**bikecount** is a small web dashboard for bicycle counts from the Fremont Bridge in Seattle. It reads the city's hourly count data and produces several pages used for a quarterly report.

Start the dashboard in **Terminal 1**:

\`\`\`
python3 -m bikecount.server
\`\`\`

Open http://localhost:8000 and look through these pages:

- Front page
- Hour of day
- Monthly totals
- By-year table
- East versus west

There is also a CSV download at \`/daily.csv\`.

Leave the server running in Terminal 1.

The pages are plain reports. The one control on them is the date window at the top of every page:

![The nav and the date window, which every page carries](${PROJECT_WORDS.bikecount.img.window})

The pages open on the most recent year. Two of the timed stages are about 2018, so set the window when you get there.

The hour-of-day page carries two charts, weekdays and weekends, which are separate things on the checklists you will be asked to fill in:

![The hour-of-day page's weekday and weekend charts, side by side](${PROJECT_WORDS.bikecount.img.hourlySplit})

**Every page is one file, and file names map to pages.** When any view — a diff, a save's echo, a feature's card — names a file, this list says which part of the dashboard it is:

- \`bikecount/pages/overview.py\` — the front page: the busiest-day figure and the last-fortnight chart
- \`bikecount/pages/hourly.py\` — the weekday and weekend hour-of-day charts
- \`bikecount/pages/monthly.py\` — the month-by-month chart
- \`bikecount/pages/sides.py\` — the east v west comparison
- \`bikecount/pages/yearly.py\` — the one-row-per-year table
- \`bikecount/metrics.py\` — works out every number the pages show
- \`bikecount/charts.py\` — draws the charts, including the marks on unusual days
- \`bikecount/events.py\` — the project's list of unusual days
- \`bikecount/data.py\` — reads \`data/counts.csv\`
- \`check.py\` — checks that every page can render successfully

You can check the whole project at any time, in **Terminal 2**:

\`\`\`
python3 check.py
\`\`\`
`,
  footfall: `
## The project

**footfall** is a small web dashboard for pedestrian counts from Spencer Street in Melbourne. It reads the city's hourly count data and produces several pages used for a quarterly report.

Start the dashboard in **Terminal 1**:

\`\`\`
python3 -m footfall.server
\`\`\`

Open http://localhost:8000 and look through these pages:

- Front page
- Hour of day
- Monthly totals
- By-year table
- North versus south

There is also a CSV download at \`/daily.csv\`.

Leave the server running in Terminal 1.

The pages are plain reports. The one control on them is the date window at the top of every page:

![The nav and the date window, which every page carries](${PROJECT_WORDS.footfall.img.window})

The pages open on the most recent year. Two of the timed stages are about 2018, so set the window when you get there.

The hour-of-day page carries two charts, weekdays and weekends, which are separate things on the checklists you will be asked to fill in:

![The hour-of-day page's weekday and weekend charts, side by side](${PROJECT_WORDS.footfall.img.hourlySplit})

**Every page is one file, and file names map to pages.** When any view — a diff, a save's echo, a feature's card — names a file, this list says which part of the dashboard it is:

- \`footfall/pages/overview.py\` — the front page: the busiest-day figure and the last-fortnight chart
- \`footfall/pages/hourly.py\` — the weekday and weekend hour-of-day charts
- \`footfall/pages/monthly.py\` — the month-by-month chart
- \`footfall/pages/sides.py\` — the north v south comparison
- \`footfall/pages/yearly.py\` — the one-row-per-year table
- \`footfall/metrics.py\` — works out every number the pages show
- \`footfall/charts.py\` — draws the charts, including the marks on unusual days
- \`footfall/events.py\` — the project's list of unusual days
- \`footfall/data.py\` — reads \`data/counts.csv\`
- \`check.py\` — checks that every page can render successfully

You can check the whole project at any time, in **Terminal 2**:

\`\`\`
python3 check.py
\`\`\`
`,
}

const WARM_UP_STATE = `
## Start the practice state

In **Terminal 2**, run:

\`\`\`
./stage 0
\`\`\`

This prepares the project for practice. When the first timed stage begins, \`./stage 1\` puts the project back to this same state, so nothing you try during practice can carry into a stage.
`

const GIT_OPENING = `
This practice covers the Git actions you will use during the timed stages: reading history, finding earlier work, and reverting it.
`

const GIT_BODY = `
## The editor

You will use three parts of the editor:

- **Source Control** shows your current changes and lets you commit them.
- **Graph** shows the commit history.
- **Timeline**, at the bottom of the Explorer, shows commits that changed the open file.

You can use either the editor or the terminal during the stages.

## 1. Read what the project is made of

Click a commit in the Graph to see the files and lines it changed.

The terminal provides the same information:

\`\`\`
git log --oneline
git show <commit hash>
\`\`\`

Find and read:

- the commit that added the CSV download
- the commit that added the by-year table

The first timed stage asks you to do this across the whole project: which piece of work put which part of the dashboard there.

## 2. Find earlier work

Git can search history for commits that added or removed a piece of text:

\`\`\`
git log --oneline -S "average"
\`\`\`

Useful commands include:

\`\`\`
git log --stat
git blame <file>
\`\`\`

- \`git log --stat\` shows which files each commit changed.
- \`git blame\` shows which commit last changed each line.
- The editor Timeline shows commits that changed the current file.
- The grey annotation beside the current line shows the commit that last changed it.

The second timed stage asks you to find a particular piece of work.

## 3. Remove work and restore it

First, try a simple revert:

\`\`\`
git revert HEAD
\`\`\`

This creates a new commit that reverses the latest commit.

Run the same command again to restore that work:

\`\`\`
git revert HEAD
\`\`\`

Now practice a revert that has conflicts.

Use \`git log --oneline\` to find the commit that first added the hour-of-day page, then run:

\`\`\`
git revert <commit hash>
\`\`\`

Git will stop when later work overlaps with the commit you are removing.

Run:

\`\`\`
git status
\`\`\`

Files under **Unmerged paths** need your attention.

For a file marked **both modified**:

1. Open the file.
2. Find the conflict markers from \`<<<<<<<\` through \`>>>>>>>\`.
3. Keep the code that should remain.
4. Delete the conflict-marker lines.
5. Save the file.
6. Run:

\`\`\`
git add <file>
\`\`\`

For a file marked **deleted by them**, choose whether the file belongs to the work being removed. For this practice example, remove the hour-of-day page:

\`\`\`
git rm <file>
\`\`\`

When \`git status\` shows no unmerged paths, finish the revert:

\`\`\`
git revert --continue
python3 check.py
\`\`\`

If you want to cancel a revert while it is still in progress, run:

\`\`\`
git revert --abort
\`\`\`

The third timed stage also asks you to remove work that later commits depend on.
`

const SGT_OPENING = `
## What sgt records

\`sgt\` works on top of a Git repository. It organizes history around the parts of the project, not around commits.

Two words appear throughout the interface, and every view draws the same picture with them:

A **feature** is one part of the project, such as "hourly charts." Each feature is one row in the history views.

A **checkpoint** is one stretch of work on one feature, such as "split weekday and weekend averages." Checkpoints are the blocks along a feature's row. Some screens call them **chapters**.

One piece of work can land on several features at once. The views draw those checkpoints linked together with one name for the whole thing, and \`sgt revert\` and \`sgt restore\` take that name directly.

This practice covers the commands used in the timed stages. Most commands also print suggested next steps.
`

const SGT_BODY = `
## The editor

The editor carries the **semi-git** extension. Click the semi-git icon in the left bar. You will use these views:

- **Now** shows the current state.
- **Features** shows features and their checkpoints.
- **Changes** shows edits that have not been saved into the history.

The **workbench** panel at the bottom is the history as a map:

![The sgt workbench: one row per feature, its checkpoints as blocks along the row, time running left to right](/materials/sgt_workbench.png)

- One row per **feature**; the row's colour is that feature's identity everywhere in the panel.
- Click a row and its card lists the **files** that feature's work touches — \`pages/<name>.py\` is the page of the same name, so the card answers "which part of the dashboard is this" directly.
- The blocks along a row are its **checkpoints**. Hover one to see its name; click it to select it.
- A hollow block is work that was reverted. A dashed block right of the "now" line is work on disk that has not been saved yet.
- A small ◆ on the time axis marks one piece of work that landed on several rows. Hover it and its blocks light up together; click it to see (and revert) the whole thing.

You can click features and checkpoints to inspect them, and right-click for actions such as **Revert** and **Restore**.

## 1. Read what the project is made of

Click a checkpoint in **Features** or in the workbench.

The terminal shows the same history, grouped by feature — one row per feature, its checkpoints along it:

\`\`\`
sgt log
\`\`\`

Each feature and checkpoint has a short ID. Use it with:

\`\`\`
sgt show <short id>
\`\`\`

You can also show a feature by its exact name:

\`\`\`
sgt show "<feature name>"
\`\`\`

The same command answers for a ◆ piece of cross-feature work. It says what that work was, which files it touches, the saves it spans, and what taking it out would remove.

Find and read:

- the work that added the CSV download
- the work that added the by-year table

To list what happened one save at a time, newest first:

\`\`\`
sgt log --rail
\`\`\`

The first timed stage asks you to do this across the whole project: which piece of work put which part of the dashboard there.

## 2. Find earlier work

Search in your own words:

\`\`\`
sgt find "the bit that works out the averages"
\`\`\`

The results can include functions, features, and individual saves.

The search box in the workbench performs the same kind of search — it also finds saves by their message or hash, and cross-feature work by its name.

To open one feature — or one piece of cross-feature work — with the map still on screen and its checkpoints listed underneath:

\`\`\`
sgt log --focus "<name>"
\`\`\`

A checkpoint handle looks like:

\`\`\`
f-08915a9f@1
\`\`\`

The third timed stage refers to one of the ◆ rows \`sgt log\` draws under the lanes — one piece of work across several features.

## 3. Remove work and restore it

Pick any checkpoint from the map — \`sgt log --focus "<feature name>"\` lists a feature's checkpoints with their handles.

Use a handle to preview a revert:

\`\`\`
sgt revert "<checkpoint handle>"
\`\`\`

The preview leaves the project unchanged. Read it before continuing.

The preview tells you:

- which work will be removed
- which work will remain
- when only part of a checkpoint will be removed
- when remaining code still depends on something being removed

For example, **2/6 edits removed** means that two of the six edits in that checkpoint belong to the work being removed.

A line beginning with ⚠ points out code that may stop working after the revert.

Apply the revert with:

\`\`\`
sgt revert "<checkpoint handle>" --yes
python3 check.py
\`\`\`

If the check reports an error, read the warning and the failing file. The error may come from remaining code that still uses the work you removed.

Restore the checkpoint with:

\`\`\`
sgt restore "<checkpoint handle>" --yes
python3 check.py
\`\`\`

Useful recovery commands are:

\`\`\`
sgt undo
sgt now
\`\`\`

- \`sgt undo\` reverses your most recent sgt action.
- \`sgt now\` shows the current state.

The third and fourth timed stages ask you to remove and restore work.
`

export function tutorialFor(condition: Condition, project: Project): string {
  const opening = condition === 'git' ? GIT_OPENING : SGT_OPENING
  const body = condition === 'git' ? GIT_BODY : SGT_BODY

  return (
    // Editor first: the pilot sheets started the server in the session shell and then sent
    // every later command to a terminal the server was occupying. The editor's integrated
    // terminals are shimmed and recorded (bin/study-terminal), so the whole practice lives in
    // one window: server in Terminal 1, commands in Terminal 2.
    [opening.trim(), OPEN_EDITOR.trim(), PROJECT_TOUR[project].trim(), WARM_UP_STATE.trim(), body.trim()].join(
      '\n\n',
    ) + '\n'
  )
}

export const TUTORIAL_LEDE =
  'Take a few minutes to practice on the project before the timed stages begin. ' +
  'Your facilitator can answer questions about the setup during this practice.'

export function sheetTutorialMd(condition: Condition, project: Project): string {
  return `# Practice: ${condition}, ${project}\n\n${TUTORIAL_LEDE}\n\n${tutorialFor(condition, project)}\n`
}

export const spell = (n: number) =>
  ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'][n] ??
  String(n)

export const TASK_PREAMBLE = (app: string, maintainer: string, blurb: string) =>
  `
You are now responsible for **${app}**, ${blurb}. ${maintainer} built the project over six weeks before leaving the team. Its numbers are used in a quarterly report.

You just practiced on this project. Keep using the same folder and the same terminal.

You will complete ${spell(STAGE_COUNT)} stages in order.

Each stage tells you:

- what happened
- what you need to do
- how to tell when you are finished
- which commands may help

Start each stage by running the \`./stage\` command shown on the card. This prepares the correct starting state. The timer begins after that command finishes.

The task itself is timed. The questions after the task are untimed. When the timer ends, continue to the questions and then move to the next stage.

Please say what you are thinking as you work.
`.trim()

export function sheetTasksMd(project: Project, condition: Condition): string {
  const { app, maintainer, blurb } = SCENARIO[project]
  const out = [
    `# Your stages: ${project}, ${condition}`,
    '',
    TASK_PREAMBLE(app, maintainer, blurb),
    '',
    'After each stage, answer the short questions on the screen. These questions are untimed.',
  ]

  for (const r of REQUESTS) {
    out.push('', `## ${requestHeading(r)}: ${r.title[project]}`, '')
    out.push(`You have ${r.capMin} minutes for this task.`)
    out.push('', r.body[project])
    // "What `./stage 1` does:" and not "when you run it, it will:" -- the
    // `does` lines are written in the third person ("resets the project"), which
    // read as "it will: resets the project" under the old lead-in. The website
    // renders the same lines under the same words (`Tasks.tsx`).
    out.push('', `What \`${r.run.script[project]}\` does:`, '')
    out.push(...r.run.does[project].map((d) => `- ${d}`))
    out.push('', 'Commands that may help:', '')
    out.push(...r.tips[condition].map((t) => `- ${t}`))

    if (r.identify) {
      out.push('', `**${r.identify[project]}:** ______________________`)
    }
  }

  return out.join('\n') + '\n'
}

export const INTERVIEW_MD = `
For the final part of the session, we will look at the history of a real repository.

If you brought a repository and gave consent to use it, your facilitator will open the history view created during setup.

If you chose the prepared repository, your facilitator will open that instead.

This part is an open interview without a timer. Walk us through what you see. Tell us:

- which parts match how you think about the work
- which parts seem grouped or named correctly
- which parts seem wrong or misleading
- what you would want to change

Your repository stays unchanged. The study keeps the recorded conversation and removes the temporary history view during cleanup.
`.trim()

export const HANDOVER_MD = `
Almost finished. Please complete these two steps before closing the study.

## 1. Send the remaining study data

In the study terminal, run:

\`\`\`
study-sync --final
\`\`\`

The status indicator will turn green when the upload finishes.

If it stays red for more than a minute, tell your facilitator. The local copy remains on the machine until cleanup.

## 2. Remove the study files

After the status indicator turns green, run:

\`\`\`
study-cleanup
\`\`\`

This removes the study projects, session keys, editor profile, and the temporary history view created for the interview.

Your own repository remains unchanged.
`.trim()

export const DEBRIEF_MD = `
That completes the study. Thank you for taking part.

## What we are studying

Git records project history through commits and changes to files. We are studying another way to organize that history around functions, classes, and the pieces of work they belong to.

During the study, you used ordinary Git and our experimental history tool on the same ${spell(STAGE_COUNT)} tasks:

- getting to know an unfamiliar project from its history
- finding the work behind a defect
- removing a piece of work
- restoring it

We compare how the two setups support these tasks.

The changes in the first stage were created earlier by an AI coding assistant and replayed onto the study project. Every participant receives the same changes.

## What happens to your data

Your recordings, questionnaire answers, and command logs are stored under a participant code.

Your name is stored separately with the consent record.

Study results are reported across participants. If you agreed to the optional quotation consent, a publication may include a short anonymized quote from your session.

If you later want your study data removed, email us and include your participant code.
`.trim()