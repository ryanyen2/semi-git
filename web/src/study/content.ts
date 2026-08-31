import type { Condition, Project } from '../lib/types'
import {
  BLOCK_ESTIMATE_MIN,
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

1. Record changes made earlier by an AI coding assistant.
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

- The AI changes were made before the study. Every participant sees the same changes.
- Each stage starts from a prepared project state, so your work in one stage does not affect the next.
- If you get stuck, say what you are trying to do and continue as far as you can.
- The study software stays inside the study folder and uses its own Python environment.
- A repository you provide for the final interview is read to build its history view. The study does not edit it.
`.trim()

export const HANDOUT_MD = `# Welcome\n\n${WELCOME_MD}\n`

const PROJECT_TOUR: Record<Project, string> = {
  bikecount: `
## The project

**bikecount** is a small web dashboard for bicycle counts from the Fremont Bridge in Seattle. It reads the city's hourly count data and produces several pages used for a quarterly report.

Start the dashboard:

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

Press Ctrl-C in the terminal when you are finished looking around.

The main files are:

- \`bikecount/pages/\` — one file for each page
- \`bikecount/metrics.py\` — calculates the numbers shown on the pages
- \`bikecount/data.py\` — reads \`data/counts.csv\`
- \`bikecount/charts.py\` — creates the charts
- \`check.py\` — checks that every page can render successfully

You can check the whole project with:

\`\`\`
python3 check.py
\`\`\`

The stages only require reading code.
`,
  footfall: `
## The project

**footfall** is a small web dashboard for pedestrian counts from Spencer Street in Melbourne. It reads the city's hourly count data and produces several pages used for a quarterly report.

Start the dashboard:

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

Press Ctrl-C in the terminal when you are finished looking around.

The main files are:

- \`footfall/pages/\` — one file for each page
- \`footfall/metrics.py\` — calculates the numbers shown on the pages
- \`footfall/data.py\` — reads \`data/counts.csv\`
- \`footfall/charts.py\` — creates the charts
- \`check.py\` — checks that every page can render successfully

You can check the whole project with:

\`\`\`
python3 check.py
\`\`\`

The stages only require reading code.
`,
}

const WARM_UP_STATE = `
## Start the practice state

In the study terminal, run:

\`\`\`
./stage 0
\`\`\`

This prepares the project for practice. When the first timed stage begins, \`./stage 1\` will replace anything you changed during practice with the correct starting state.
`

const GIT_OPENING = `
This practice covers the Git actions you will use during the timed stages: reading history, recording work, finding earlier work, and reverting it.
`

const GIT_BODY = `
## Open the editor

Run:

\`\`\`
study-code
\`\`\`

This opens the project in VS Code.

You will use three parts of the editor:

- **Source Control** shows your current changes and lets you commit them.
- **Graph** shows the commit history.
- **Timeline**, at the bottom of the Explorer, shows commits that changed the open file.

You can use either the editor or the terminal during the stages.

## 1. Read a change

Click a commit in the Graph to see the files and lines it changed.

The terminal provides the same information:

\`\`\`
git log --oneline
git show <commit hash>
\`\`\`

On this machine, Git prints directly in the terminal, so long output stays in the terminal history.

Find and read:

- the commit that added the CSV download
- the commit that added the by-year table

These commits show how most of the project is organized.

## 2. Record some work

Open \`README.md\` and change one word.

Then commit the change using Source Control, or run:

\`\`\`
git add README.md
git commit -m "reword a line in the readme"
\`\`\`

The first timed stage asks you to record changes in the same way.

## 3. Find earlier work

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

## 4. Remove work and restore it

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

## Before the timed stages

Run:

\`\`\`
python3 check.py
\`\`\`

Make sure the project passes the check. Tell the facilitator if any command behaved differently from the instructions.
`

const SGT_OPENING = `
## What sgt records

\`sgt\` works on top of a Git repository. It organizes history around functions, classes, and the pieces of work they belong to.

Two terms appear throughout the interface:

A **feature** is a larger body of work, such as "hourly charts."

A **checkpoint** is one step within a feature, such as "split weekday and weekend averages." Some screens call checkpoints **chapters**. Both words refer to the same thing.

This practice covers the commands used in the timed stages. Most commands also print suggested next steps.
`

const SGT_BODY = `
## Open the editor

Run:

\`\`\`
study-code
\`\`\`

This opens the project in VS Code with the **semi-git** extension.

Click the semi-git icon in the left bar. You will use these views:

- **Now** shows the current state.
- **Features** shows features and their checkpoints.
- **Changes** shows edits that have not been saved into the history.

The **workbench** panel at the bottom shows features as rows across time. The chips on each row are checkpoints.

You can click features and checkpoints to inspect them. You can also right-click them to find actions such as **Revert** and **Restore**.

## 1. Read a change

Click a checkpoint in **Features** or in the workbench.

The terminal lists the same history:

\`\`\`
sgt log
\`\`\`

Each row has a short seven-character ID. Use that ID with:

\`\`\`
sgt show <short id>
\`\`\`

You can also show a feature by its exact name:

\`\`\`
sgt show "<feature name>"
\`\`\`

Find and read:

- the work that added the CSV download
- the work that added the by-year table

You can also see the history grouped by feature:

\`\`\`
sgt log --map
\`\`\`

## 2. Record some work

Open \`README.md\` and change one word.

The **Changes** view will show the edit.

Record it with:

\`\`\`
sgt save -m "reword a line in the readme"
\`\`\`

The first timed stage asks you to record changes in the same way.

## 3. Find earlier work

Search in your own words:

\`\`\`
sgt find "the bit that works out the averages"
\`\`\`

The results can include functions, features, and individual saves.

The search box in the workbench performs the same kind of search.

To see the complete names and handles for features and checkpoints, run:

\`\`\`
sgt intent list
\`\`\`

A checkpoint handle looks like:

\`\`\`
f-08915a9f@1
\`\`\`

The bottom of \`sgt intent list\` can also contain groups that combine work from several features. The third timed stage may refer to one of these groups.

## 4. Remove work and restore it

Choose a checkpoint from somewhere in the middle of \`sgt intent list\`.

Use its handle to preview a revert:

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

## 5. Help

For command help, run:

\`\`\`
sgt --help
sgt <command> --help
\`\`\`

## Before the timed stages

Run:

\`\`\`
python3 check.py
\`\`\`

Make sure the project passes the check. Tell the facilitator if any command or output was unclear.
`

export function tutorialFor(condition: Condition, project: Project): string {
  const opening = condition === 'git' ? GIT_OPENING : SGT_OPENING
  const body = condition === 'git' ? GIT_BODY : SGT_BODY

  return (
    [opening.trim(), PROJECT_TOUR[project].trim(), WARM_UP_STATE.trim(), body.trim()].join(
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
    out.push('', `When you run \`${r.run.script[project]}\`, it will:`, '')
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

- recording work made by an AI coding assistant
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