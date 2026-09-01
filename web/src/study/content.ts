import type { Condition, Project } from '../lib/types'
import {
  PROJECT_WORDS,
  REQUESTS,
  SCENARIO,
  STAGE_COUNT,
  requestHeading,
} from './tasks'

export const PLAN_FOR = 'an hour and a half'

export const WELCOME_MD = `
Thanks for taking part. The session takes about ${PLAN_FOR}, including breaks.

You will use two tools to explore and change a small project's history. With each tool, you will:

1. Find the changes that added parts of the dashboard.
2. Find the change that caused an incorrect result.
3. Remove the requested work.
4. Restore it.

You will practice before starting and answer a few questions as you go. We will finish with an interview.

As you work, say what you are looking for, what you plan to do, and what you expect to happen. Tell us when something is confusing or surprising.
`.trim()

export const HANDOUT_MD = `# Welcome\n\n${WELCOME_MD}\n`

const OPEN_EDITOR = `
## Open the editor

Run \`study-code\` in the session shell and leave that window open — it keeps recording. Everything else happens in the editor. Open two terminals there (**Terminal → New Terminal**, then the **+** button): one for the dashboard, one for every other command.
`

const PROJECT_TOUR: Record<Project, string> = {
  bikecount: `
## The project

**bikecount** shows bicycle counts from the Fremont Bridge in Seattle. Its charts and tables are used in a quarterly report.

Start it with \`python3 -m bikecount.server\` in the first terminal, then open http://localhost:8000 and look through the pages. Use the date range at the top to choose which dates to show.

![Date range controls](${PROJECT_WORDS.bikecount.img.window})

The hour-of-day page has separate charts for weekdays and weekends.

![Weekday and weekend charts](${PROJECT_WORDS.bikecount.img.hourlySplit})

Each page has a file in \`bikecount/pages/\`:

| File | Page |
|---|---|
| \`overview.py\` | Front page |
| \`hourly.py\` | Hour of day |
| \`monthly.py\` | Monthly totals |
| \`yearly.py\` | By-year table |
| \`sides.py\` | East versus west |

\`bikecount/metrics.py\` calculates the numbers, and \`bikecount/charts.py\` draws the charts.
`,
  footfall: `
## The project

**footfall** shows pedestrian counts from Spencer Street in Melbourne. Its charts and tables are used in a quarterly report.

Start it with \`python3 -m footfall.server\` in the first terminal, then open http://localhost:8000 and look through the pages. Use the date range at the top to choose which dates to show.

![Date range controls](${PROJECT_WORDS.footfall.img.window})

The hour-of-day page has separate charts for weekdays and weekends.

![Weekday and weekend charts](${PROJECT_WORDS.footfall.img.hourlySplit})

Each page has a file in \`footfall/pages/\`:

| File | Page |
|---|---|
| \`overview.py\` | Front page |
| \`hourly.py\` | Hour of day |
| \`monthly.py\` | Monthly totals |
| \`yearly.py\` | By-year table |
| \`sides.py\` | North versus south |

\`footfall/metrics.py\` calculates the numbers, and \`footfall/charts.py\` draws the charts.
`,
}

const WARM_UP_STATE = `
## Start practice

Run this command in the study terminal:

\`\`\`
./stage 0
\`\`\`

The project will reset before the first task.
`

const GIT_OPENING = `
Practice reading, finding, and undoing changes with Git. You can use the history views or the terminal.
`

const GIT_BODY = `
## 1. Read the history

Click a commit in **Graph** to see its changes, or run:

\`\`\`
git log --oneline
git show <commit hash>
\`\`\`

Find the commits that added the CSV download and the by-year table. Read what each one changed.

## 2. Find a change

Search for commits that added or removed the text "average":

\`\`\`
git log --oneline -S "average"
\`\`\`

Use \`git log --stat\` to see which files each commit changed. Use \`git blame <file>\` to see which commit last changed each line.

## 3. Undo and restore a change

Undo the latest commit:

\`\`\`
git revert HEAD
\`\`\`

Git creates a new commit that reverses the change. Undo that new commit to restore the change:

\`\`\`
git revert HEAD
\`\`\`

## 4. Resolve a conflict

Find the commit that added the hour-of-day page and revert it:

\`\`\`
git revert <commit hash>
git status
\`\`\`

If Git reports conflicts, resolve the files listed under **Unmerged paths**:

- For **both modified**, open the file, keep the code that should remain, and delete the conflict markers. Save the file and run \`git add <file>\`.
- For **deleted by them**, choose whether to keep or remove the file. In this example, remove the hour-of-day page with \`git rm <file>\`.

After resolving the conflicts, finish the revert and check that the pages still render:

\`\`\`
git revert --continue
python3 check.py
\`\`\`

To cancel a revert while resolving conflicts, run \`git revert --abort\`.
`

const SGT_OPENING = `
Practice reading, finding, and undoing changes with sgt. You can use the history views or the terminal.

A **feature** is a part of the project, such as "hourly charts." A **checkpoint** is a set of changes to a feature, such as "split weekday and weekend averages." Some views call checkpoints **chapters**.
`

const SGT_BODY = `
## 1. Read the history

The **workbench** shows a row for each feature. Blocks along a row are its checkpoints.

![Features and checkpoints in the workbench](/materials/sgt_workbench.png)

Click a feature or checkpoint to see its changes and files. A ◆ marks work that changed several features. Click it to see those changes together. A hollow block marks work that has been reverted.

You can also read the history in the terminal:

\`\`\`
sgt log
sgt show <short id>
\`\`\`

Use the ID shown in the history, or enter a name in quotes:

\`\`\`
sgt show "<feature name>"
\`\`\`

Find the work that added the CSV download and the by-year table. Read what each one changed.

## 2. Find a change

Search by describing what you are looking for:

\`\`\`
sgt find "calculate averages"
\`\`\`

You can also use the search box in the workbench.

To list a feature's checkpoints and their IDs, run:

\`\`\`
sgt log --focus "<feature name>"
\`\`\`

Checkpoint IDs look like \`f-08915a9f@1\`.

## 3. Undo and restore a change

Choose a checkpoint and preview removing it:

\`\`\`
sgt revert "<checkpoint id>"
\`\`\`

Read which changes would be removed and which would remain. Warnings identify code that may stop working. The preview leaves the project unchanged.

Apply the revert and check that the pages still render:

\`\`\`
sgt revert "<checkpoint id>" --yes
python3 check.py
\`\`\`

Restore the checkpoint:

\`\`\`
sgt restore "<checkpoint id>" --yes
python3 check.py
\`\`\`

Both commands also accept the name of work marked with a ◆. In the workbench, right-click a feature or checkpoint for **Revert** and **Restore**.

Use \`sgt undo\` to reverse your most recent sgt action. Use \`sgt now\` to see the current state.
`

export function tutorialFor(condition: Condition, project: Project): string {
  const opening = condition === 'git' ? GIT_OPENING : SGT_OPENING
  const body = condition === 'git' ? GIT_BODY : SGT_BODY

  return (
    [opening, OPEN_EDITOR, PROJECT_TOUR[project], WARM_UP_STATE, body]
      .map((section) => section.trim())
      .join('\n\n') + '\n'
  )
}

export const TUTORIAL_LEDE =
  'Try these steps before starting the tasks.'

export function sheetTutorialMd(condition: Condition, project: Project): string {
  return `# Practice: ${condition}, ${project}\n\n${TUTORIAL_LEDE}\n\n${tutorialFor(condition, project)}\n`
}

export const spell = (n: number) =>
  ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'][n] ??
  String(n)

export const TASK_PREAMBLE = (app: string, maintainer: string, blurb: string) =>
  `
You are taking over **${app}**, ${blurb}, from ${maintainer}. Its numbers are used in a quarterly report.

Complete the ${spell(STAGE_COUNT)} tasks in order. Start each one with the \`./stage\` command shown on its card. The timer starts after the command finishes.

When you finish or the timer ends, answer the questions and continue to the next task.
`.trim()

export function sheetTasksMd(project: Project, condition: Condition): string {
  const { app, maintainer, blurb } = SCENARIO[project]
  const out = [
    `# Tasks: ${project}, ${condition}`,
    '',
    TASK_PREAMBLE(app, maintainer, blurb),
  ]

  for (const r of REQUESTS) {
    out.push('', `## ${requestHeading(r)}: ${r.title[project]}`, '')
    out.push(`You have ${r.capMin} minutes for this task.`)
    out.push('', `Start by running \`${r.run.script[project]}\`.`, '')
    out.push(r.body[project])

    if (r.identify) {
      out.push('', `**${r.identify[project]}:** ______________________`)
    }
  }

  return out.join('\n') + '\n'
}

export const INTERVIEW_MD = `
We will look at another repository's history and talk about your experience with the two tools.

As you explore, tell us what you understand, what is unclear, and what you would want to know.
`.trim()

export const HANDOVER_MD = `
## Send the study data

Run this command in the study terminal:

\`\`\`
study-sync --final
\`\`\`

Wait for the status indicator to turn green before continuing.

## Remove the study files

Run:

\`\`\`
study-cleanup
\`\`\`
`.trim()

export const DEBRIEF_MD = `
Thank you for taking part.

We are studying how people use project history to understand a project, find the cause of a problem, and remove or restore work. We compare Git with sgt, which groups changes by feature.

Your recordings, answers, and command logs are stored under a participant code. Your name is stored separately with your consent record. We report results across participants and use short anonymized quotes with your consent.

To request removal of your study data, email us with your participant code.
`.trim()
