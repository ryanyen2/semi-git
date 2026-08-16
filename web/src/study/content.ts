// Participant-facing prose: the welcome, and the two practice sheets.
//
// Taken from docs/study/materials/ so there is one wording, not two. The only
// edits are the ones the bundle made necessary: the practice sheets no longer
// explain `../bin/sgt`, because the study shell puts the right binary on PATH,
// and the timings match docs/study/protocol.md §4.

import type { Condition } from '../lib/types'

export const WELCOME_MD = `
Thanks for taking part. Plan for about two hours, including breaks.

## What you'll do

- Work on a small program you have never seen. Someone built it over six weeks and then left.
- Handle a few requests that have come in about it.
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
| 10 | Consent and a few questions about your background |
| 10 | Setting up, then a practice project |
| 45 | The requests |
| 12 | Two questionnaires, five questions, and a short summary |
| 15 | Same again, other setup, other project |
| 45 | The requests |
| 12 | Questionnaires again |
| 8 | Comparing the two, and a chat about how it went |

Each request has its own time limit, and you can see it counting down.

- Stopping in the middle is fine.
- Finishing early is fine.
- We expect people to run out of time on some of them. That is a normal result, not a problem.

## One thing to know

Nothing you do can break anything that matters. Every project is a fresh copy. If you get one into a state you can't get out of, say so and we'll reset it. Getting stuck is information for us, so please don't hide it.

## Your own machine stays untouched

The setup step installs everything inside one folder and uses its own Python. It does not change your shell, your global packages, or your existing AI assistant account. The assistant runs on a key we issue for this session and revoke afterwards, so nothing is billed to you.
`.trim()

const TUTORIAL_GIT = `
Ten minutes on a practice project first. Ask anything now. Once the real requests start we can only answer questions about the requests themselves.

You already know git. This is only to check that nothing on this machine is set up oddly, and to remind you what is available.

## 1. Look around

\`\`\`
git log --oneline
git log --stat
\`\`\`

## 2. Ask what one change was

\`\`\`
git show <commit>
git show <commit> -- <file>
\`\`\`

## 3. Follow one file or one piece of text over time

\`\`\`
git log -p -- <file>
git log -S "<some text>"
git blame <file>
\`\`\`

\`git log -S\` finds commits where the number of times some text appears changed. It is the usual way to find when something arrived or disappeared.

## 4. Undo something

\`\`\`
git revert <commit>
\`\`\`

Makes a new commit that undoes an old one. It can conflict if later commits touched the same lines. Fix the conflict, or \`git revert --abort\`.

Branches, for trying something you might throw away:

\`\`\`
git checkout -b try-something
git checkout main
git branch -D try-something
\`\`\`

## 5. Help

\`\`\`
git help <command>
\`\`\`

Your assistant knows git well, so you can just ask it.

## Before we start

Tell us if any of that behaved differently from what you expected.
`.trim()

const TUTORIAL_SGT = `
Ten minutes on a practice project first. Ask anything now. Once the real requests start we can only answer questions about the requests themselves.

## What it is

\`sgt\` sits on top of an ordinary git repository. Git records which lines in which files changed. \`sgt\` records which functions and classes changed, and groups related work under a name. It calls those groups features.

Ten minutes will not make you fluent, and we do not expect it to. Every command ends by printing what you might want to run next, so you can follow that rather than memorising anything.

## 1. Look around

\`\`\`
sgt now          a short summary of where things stand
sgt log          your saved work, newest first
sgt log --map    the same history, one row per feature over time
\`\`\`

## 2. Record a change

Edit one of the functions, then:

\`\`\`
sgt save -m "what you changed, in your own words"
\`\`\`

Your words become the name of the work. It then tells you which feature the change landed in.

## 3. Ask what something is

Every command prints short ids. Hand any of them back:

\`\`\`
sgt show <id>
\`\`\`

You get what it covers, what would come with it, and what you can do next.

## 4. Take something out

\`\`\`
sgt revert <what>
\`\`\`

- It shows you what would happen first, and only does it if you add \`--yes\`.
- \`<what>\` can be a function like \`cart.py::total\`, a feature name, or a plain English phrase like "the thing that formats dates".

Two more:

\`\`\`
sgt restore <what>    bring something back
sgt undo              reverse the last thing sgt did
\`\`\`

Try it now: remove a function, read what it says it would do, do it, then undo it.

## 5. Help

\`\`\`
sgt --help
sgt <command> --help
\`\`\`

Your assistant knows these commands too.

## Before we start

Tell us if anything printed something you could not make sense of.
`.trim()

export function tutorialFor(condition: Condition): string {
  return condition === 'git' ? TUTORIAL_GIT : TUTORIAL_SGT
}

export const TASK_PREAMBLE = (app: string, maintainer: string, blurb: string) =>
  `
You are the new maintainer of a program called **${app}**. It is ${blurb}. ${maintainer} built it over the last six weeks, partly by working with an AI assistant, and has now left the team.

You have the code, its full history, and your assistant. Work through the requests in order. The last two are optional and only if you have time.

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

That removes the two project folders and the session's API keys from your machine. The projects get reused with other participants, so please do run it.

Your own AI assistant setup is untouched. Nothing was installed outside the study folder.
`.trim()

export const DEBRIEF_MD = `
That is everything. Thank you.

## What this was about

Version control records history as lines in files. When you work with an AI assistant you describe what you want in sentences, and then the record of what you did comes back to you as diffs. We built a tool that records history as the pieces of work someone meant to do, and we wanted to know whether that helps a person who arrives afterwards.

One of the two setups you used was that tool. The other was ordinary git. We deliberately did not say which was which, and we asked the same six requests of both, because we are measuring the difference between the two representations and not the difference between you and anyone else.

## What happens to your data

Your recordings, your answers, and the log of commands and prompts from your machine are stored under a participant code, not your name. Your name appears only on the consent record, which is kept separately. Results are reported in aggregate. If you ticked the optional consent line, a short quote might appear, with nothing in it that identifies you.

If you want your data removed, email us with your participant code and we will delete it. No reason needed.
`.trim()
