// The four stages, in both projects. Protocol v2 (docs/study/protocol-v2.md).
//
// Wording is the participant's handout, verbatim. The two projects say the same
// thing about different nouns and different numbers, so a stage body is written
// once as a template and filled in from PROJECT_WORDS below. It used to be two
// hand-written strings per stage, and the footfall copies still quoted
// bikecount's two averages -- the number the whole removal story is about was
// wrong in half the sessions, and nothing checked it.
//
// Nothing in a stage BODY names a git or an sgt verb: a stage states what
// happened and what to do in product terms, and the participant chooses the
// mechanism inside the tool they were given. The TIPS are the one exception,
// and they are deliberate: pilots lost minutes of a four-minute stage to
// remembering the name of a command, which is not what any of this measures.
// Both arms get the same number of reminders about their own tool.
//
// WHAT THE BLOCK MEASURES, AND WHY IT IS SHAPED THIS WAY
//
// Protocol v1 gave people an unfamiliar codebase and an open task, and pilots
// spent their timed minutes orienting and choosing strategies. Those costs
// landed on top of the thing under test, which is whether the representation
// of history helps at each step. So v2 prescribes everything except the step
// itself. Each stage starts from a scripted state (`./stage N`), tells the
// participant exactly what happened, and asks for one thing. A stage that goes
// wrong cannot spoil the next one, because the next one resets the state.
//
// Each stage runs in two phases. The work is capped at STAGE_CAP_MIN minutes
// with a visible countdown. The quiz and the three rating statements that
// follow are untimed: they are measurements of what the person took away, not
// more work to race through.
//
// There is no live AI assistant in the block. Stage 1's "assistant work" is a
// recorded agent session replayed by `./stage 1`, so every participant reads
// byte-identical changes. See protocol v2 section 3 for what that trades away.

import type { Condition, Project, RequestId } from '../lib/types'

/**
 * One thing a person can see the app do, and the page that shows it.
 *
 * The page is named on purpose. Without it, "the busiest hour" and "the
 * weekday chart" are two descriptions a participant has to guess the boundary
 * between; with it there is exactly one thing each option means. It is still
 * product language. No git or sgt verb appears anywhere in this list.
 *
 * Ids are the stored answer, so this wording can change between pilots without
 * orphaning what has already been collected. They are also the ids the key
 * generators measure, and generation fails if a page behind one disappears.
 */
export interface Behaviour {
  id: string
  label: Record<Project, string>
  command: Record<Project, string>
}

/**
 * Eleven, and the same eleven on every checklist in the block.
 *
 * One list learned once, so the later checklists cost no reading and the
 * answers are directly comparable. The keys have to sit well inside it, so
 * neither "tick everything" nor "tick nothing" is close to right; the key
 * upload refuses a key naming zero or all of them (`answerKey.ts`).
 *
 * The list is also the study's join between what a participant SEES and what
 * their setup NAMES. Every leaf feature the shipped bundles cluster is the
 * target of at least one option here, and every option is produced by at least
 * one of those features -- measured off the bundles by
 * `scripts/study/measure_orientation.py`, which is also where stage 1's
 * mapping table comes from. The date window was the one cluster with no option
 * ("Date Window Filtering" in both projects), so a participant reading the
 * feature map found a lane the checklist could not express; it is the last row
 * below. Nothing here names a git or an sgt verb, and the labels stay in
 * product language, so the two arms read the same eleven things.
 */
export const BEHAVIOURS: Behaviour[] = [
  {
    id: 'busiestDay',
    label: {
      bikecount: 'The busiest day figure on the front page',
      footfall: 'The busiest day figure on the front page',
    },
    command: { bikecount: '/', footfall: '/' },
  },
  {
    id: 'recentChart',
    label: {
      bikecount: 'The last fortnight chart on the front page',
      footfall: 'The last fortnight chart on the front page',
    },
    command: { bikecount: '/', footfall: '/' },
  },
  {
    id: 'hourWeekday',
    label: {
      bikecount: 'The weekday hour-of-day chart',
      footfall: 'The weekday hour-of-day chart',
    },
    command: { bikecount: '/hourly', footfall: '/hourly' },
  },
  {
    id: 'hourWeekend',
    label: {
      bikecount: 'The weekend hour-of-day chart',
      footfall: 'The weekend hour-of-day chart',
    },
    command: { bikecount: '/hourly', footfall: '/hourly' },
  },
  {
    id: 'busiestHour',
    label: {
      bikecount: 'The busiest hour called out above that chart',
      footfall: 'The busiest hour called out above that chart',
    },
    command: { bikecount: '/hourly', footfall: '/hourly' },
  },
  {
    id: 'monthly',
    label: {
      bikecount: 'The month-by-month chart',
      footfall: 'The month-by-month chart',
    },
    command: { bikecount: '/monthly', footfall: '/monthly' },
  },
  {
    id: 'eventMarks',
    label: {
      bikecount: 'The marks that flag unusual days on the charts',
      footfall: 'The marks that flag unusual days on the charts',
    },
    command: { bikecount: '/monthly', footfall: '/monthly' },
  },
  {
    id: 'yearTable',
    label: {
      bikecount: 'The one-row-per-year table',
      footfall: 'The one-row-per-year table',
    },
    command: { bikecount: '/yearly', footfall: '/yearly' },
  },
  {
    id: 'sideSplit',
    label: {
      bikecount: 'The east against west comparison',
      footfall: 'The north against south comparison',
    },
    command: { bikecount: '/sides', footfall: '/sides' },
  },
  {
    id: 'csv',
    label: {
      bikecount: 'The daily totals csv download',
      footfall: 'The daily totals csv download',
    },
    command: { bikecount: '/daily.csv', footfall: '/daily.csv' },
  },
  // Not a figure or a chart, and the only option that is the same control on
  // every page. It is here because the feature map has a lane for it in both
  // projects and the checklist had no way to say so, and because it is the one
  // option the work stages 2 to 4 are about does not reach -- a checklist whose
  // every option is in the key measures nothing.
  {
    id: 'dateWindow',
    label: {
      bikecount: 'The date window at the top of every page',
      footfall: 'The date window at the top of every page',
    },
    command: { bikecount: '/', footfall: '/' },
  },
]

/**
 * Commands the participant is told to run exactly as written.
 *
 * `script` is what they type. `does` is what the sheet prints underneath it,
 * so a prescribed step is never a black box. A participant who wants to know
 * what they just ran can read it, and a facilitator can check the output is
 * the output everyone else got.
 */
export interface PrescribedRun {
  script: Record<Project, string>
  does: Record<Project, string[]>
}

/** One quiz item on a stage, answered after the work with no clock running. */
export type QuizItem =
  | {
      kind: 'behaviours'
      id: string
      /** Same wording in both projects on purpose: the checklist is the
       * measurement, and two wordings would be two measurements. */
      prompt: string
      /**
       * True: scored as set F1 against the measured key in answer-key.json.
       * False: no key, and what comes out is the raw set of ticks.
       *
       * Every scored checklist needs a target whose removal leaves the app
       * running, because that is how the key is measured -- remove it on a copy,
       * re-render every page, map what moved. Only two of the eighteen groups in
       * each bundle qualify: the event-day work (stages 2 and 3) and the
       * rounding work (stage 1). Every other selection exits zero, prints
       * `✓ revert applied`, and leaves the dashboard dead
       * (docs/study/sgt-findings.md, finding 85). That is why the two targets are
       * the ones they are, and it is checked at key-generation time rather than
       * assumed.
       */
      scored: boolean
    }
  | {
      kind: 'choice'
      id: string
      prompt: string
      options: Array<{ value: string; label: string }>
      /** Scored exact against the key, or recorded as a self-report. */
      scored: boolean
    }
  | {
      kind: 'text'
      id: string
      prompt: string
      /** Never scored. Kept for the interview and the qualitative analysis. */
      scored: false
    }

/**
 * One of the rating statements a stage ends with. Protocol v2's replacement for
 * the HLAC battery: the same kind of 7-point item, asked in the minute after the
 * experience it asks about instead of ten minutes later.
 *
 * The reading stages (1 and 2) ask two statements, both of the same shape: did
 * you understand the change, and did you understand what it reaches. The
 * operating stages (3 and 4) ask three, the last of them reverse-keyed as the
 * guard against straight-lining.
 */
export interface StageRating {
  id: string
  label: string
  reverse?: boolean
  /** Which claim the item serves. Shown in the dashboard, never to the participant. */
  serves: string
}

export interface RequestSpec {
  id: RequestId
  /** "Stage 1". Written out rather than derived from the id. */
  heading: string
  title: Record<Project, string>
  body: Record<Project, string>
  /**
   * Command reminders, shown beside the card for the whole working phase.
   *
   * Per condition, because they name that condition's own commands, and the
   * same number of them in each arm. A participant who cannot remember whether
   * the flag is `-S` or `--search` is not telling us anything about how history
   * is represented, and a four-minute stage has no room for it.
   */
  tips: Record<Condition, string[]>
  /** Minutes for the work phase. The quiz that follows is untimed. */
  capMin: number
  optional: boolean
  /** The stage-reset command, printed first on every card. */
  run: PrescribedRun
  /**
   * A box holding one identifier -- a commit hash under git, a named piece of
   * work under sgt -- shown during the WORK phase, because finding it is the
   * work. Free text, compared against the key after the session rather than
   * in the browser (see protocol v2 section 4).
   */
  identify?: Record<Project, string>
  /**
   * The key must accept `locate` answers for this stage even when no in-work
   * box (`identify`) collects them -- the participant names the work in the
   * recognition question and aloud, and the facilitator scores that against
   * the key after the session.
   */
  scoredLocate?: boolean
  /** The quiz, in order. Rendered after the work phase, untimed. Two items at
   * most: pilots spent longer on a three-item quiz than on the stage. */
  quiz: QuizItem[]
  /** Whether the quiz ends with a confidence rating. Only stages whose quiz has
   * a right answer carry one; calibration needs both halves. */
  quizConfidence: boolean
  /** The rating statements. */
  ratings: StageRating[]
  /** What the stage is testing. Never shown to the participant. */
  archetype: string
  serves: string
}

export const SCENARIO: Record<Project, { app: string; maintainer: string; blurb: string }> = {
  bikecount: {
    app: 'bikecount',
    maintainer: 'Dana Whitfield',
    blurb:
      'a small web dashboard over the bicycle counter on the Fremont Bridge in Seattle',
  },
  footfall: {
    app: 'footfall',
    maintainer: 'Dana Whitfield',
    blurb:
      'a small web dashboard over the pedestrian counter on Spencer Street in Melbourne',
  },
}

/**
 * The words and the numbers each project's stages are written in.
 *
 * `reported` is the average over every day the sensors recorded, which is what
 * the published report was written against. `dashboard` is what the by-year page
 * shows while the work under study is in place. Both are measured, not invented:
 * `scripts/study/task-scripts/check` prints exactly these two numbers, and a
 * test compares them against the testbeds.
 *
 * They exist because the footfall stages used to quote bikecount's 2,882 and
 * 2,900. Footfall's numbers are 42,436 and 42,545, so every footfall participant
 * was asked to reach a number the dashboard could not print, and the reset script
 * printed a different pair beside it.
 */
export const PROJECT_WORDS: Record<
  Project,
  {
    reported: string
    dashboard: string
    body: string
    publisher: string
    document: string
    unusualDays: string
    ordinaryDay: string
    precision: string
    /** The python package, so a card can name a file without naming a project twice. */
    pkg: string
    /** What this project's own nav calls the two-sensor page. */
    sidesPage: string
    /**
     * Stage screenshots (web/public/stages/), captured from the shipped bundle's own dashboard by
     * `scripts/study/capture-page-shots.mjs`, so what a card shows is exactly what the
     * participant's browser shows -- and so a testbed rebuild can regenerate all of them in one
     * command instead of leaving the cards quoting last month's numbers.
     */
    img: {
      yearly: string
      monthly: string
      hourly: string
      hourlySplit: string
      sides: string
      window: string
    }
  }
> = {
  bikecount: {
    reported: '2,882',
    dashboard: '2,900',
    body: 'crossings',
    publisher: 'The cycling team',
    document: 'report',
    unusualDays: 'the February 2019 snowstorm and Christmas',
    ordinaryDay:
      'a snowstorm that shut the city says nothing about how many people cycle to work on an ordinary day',
    precision: 'one-bike precision',
    pkg: 'bikecount',
    sidesPage: 'east against west',
    img: {
      yearly: '/stages/bikecount-yearly.png',
      monthly: '/stages/bikecount-monthly.png',
      hourly: '/stages/bikecount-hourly.png',
      hourlySplit: '/stages/bikecount-hourly-split.png',
      sides: '/stages/bikecount-sides.png',
      window: '/stages/bikecount-window.png',
    },
  },
  footfall: {
    reported: '42,436',
    dashboard: '42,545',
    body: 'people walk past',
    publisher: 'The transport committee',
    document: 'paper',
    unusualDays: 'Grand Final Friday and Christmas',
    ordinaryDay:
      'a public holiday when the offices are shut says nothing about how many people walk to work on an ordinary day',
    precision: 'single-person precision',
    pkg: 'footfall',
    sidesPage: 'north against south',
    img: {
      yearly: '/stages/footfall-yearly.png',
      monthly: '/stages/footfall-monthly.png',
      hourly: '/stages/footfall-hourly.png',
      hourlySplit: '/stages/footfall-hourly-split.png',
      sides: '/stages/footfall-sides.png',
      window: '/stages/footfall-window.png',
    },
  },
}

/** The same body text in both projects, with that project's words in it. */
function forEachProject(write: (w: (typeof PROJECT_WORDS)['bikecount']) => string): Record<Project, string> {
  return {
    bikecount: write(PROJECT_WORDS.bikecount).trim(),
    footfall: write(PROJECT_WORDS.footfall).trim(),
  }
}

export const REQUESTS: RequestSpec[] = [
  {
    id: 's1',
    heading: 'Stage 1',
    // Five, where the other three get four. Orienting in a project nobody has
    // seen before is the one stage whose work is reading rather than doing, and
    // it is the one every stage after it depends on: they all assume the
    // participant knows what the dashboard shows. Five and not six because the
    // welcome page promises an hour and a half and the schedule test holds the
    // total to it (`web/tests/schedule.test.ts`).
    capMin: 5,
    optional: false,
    archetype: 'orient in an unfamiliar project: which work in its history put which part of the product there',
    serves: 'RQ1, claim C1',
    title: {
      bikecount: 'Get to know the project',
      footfall: 'Get to know the project',
    },
    body: forEachProject(
      (w) => `
Run the command below first. It puts the project into this stage's starting state.

    ./stage 1

**What happened:** You have just joined this project. Nothing is wrong with it, and there is nothing to fix in this stage.

**Your job:** Work out what this project is made of. Put the dashboard beside your setup's view of the history, and fill in the map below — for each part of the dashboard, where it lives in the code and which piece of work in the history put it there. The first row is filled in as an example.

| Part of the dashboard | Where it lives in the code | The work that put it there |
|---|---|---|
| **The busiest hour, and the hour-of-day chart under it** ![The hourly page: the busiest hour, and the average count by hour of day](${w.img.hourly}) | \`${w.pkg}/pages/hourly.py\` draws it; \`${w.pkg}/metrics.py\` works out the averages | *(example)* the work that added the hour-of-day page |
| **The month-by-month chart** ![The monthly page](${w.img.monthly}) | | |
| **The one-row-per-year table** ![The by-year page](${w.img.yearly}) | | |
| **The ${w.sidesPage} comparison** ![The two-sensor comparison page](${w.img.sides}) | | |

The date window at the top of every page is the one control on the dashboard. The pages open on the most recent year; set it to 2018 if you want to see the whole of a year.

**You are done when:** you could point at any part of the dashboard and say which piece of work in the history put it there, and roughly where in the code that work lives. Nothing in the map has to be written down — the questions after this stage are about one piece of work in particular.
`,
    ),
    tips: {
      git: [
        '`git log --oneline` lists the commits, newest first — one line per piece of work.',
        '`git show <hash>` shows what one of them changed.',
        '`git log --oneline -- <file>` narrows the list to one file, and `git blame <file>` says which commit last touched each line.',
        'In the editor, the Graph shows the same history, and the Timeline at the bottom of the Explorer shows the commits that touched the open file.',
      ],
      sgt: [
        '`sgt log` is the feature map: one row per part of the project, edits over time. `sgt log --rail` lists what happened, newest first.',
        '`sgt show "<name>"` says what one part covers — the files and the code inside it. `sgt show <file>::<name>` answers the other direction, for one function.',
        '`sgt find "the bit that works out the averages"` searches by description. Any wording will do.',
        '`sgt log --focus "<name>"` opens one row, or one ◆ piece of work that spans several rows.',
      ],
    },
    run: {
      script: { bikecount: './stage 1', footfall: './stage 1' },
      does: {
        bikecount: [
          "resets the project to its full recorded history, with nothing of anyone else's left in it",
        ],
        footfall: [
          "resets the project to its full recorded history, with nothing of anyone else's left in it",
        ],
      },
    },
    quiz: [
      {
        kind: 'behaviours',
        id: 'behaviours',
        // The target is named by its position in the history, not by what it
        // did, because naming what it did ("the work that rounded the front page
        // numbers") would answer the question. Both arms read the position the
        // same way: it is the top line of `git log --oneline`, and the newest
        // block on the map / the top of `sgt log --rail`.
        //
        // Both arms then get its subject for free, and that subject says "front
        // page". What it does not say is WHICH parts of the front page, and the
        // checklist offers the busiest-day figure and the last-fortnight chart
        // separately -- so the judgement the stage measures is whether the
        // representation showed what the work reached, not whether the
        // participant could find it.
        prompt:
          'The most recent piece of work in this project is one of the pieces you have just been looking at. Which parts of the dashboard does it affect? Tick the ones you would check if it were taken out.',
        scored: true,
      },
    ],
    quizConfidence: true,
    ratings: [
      {
        id: 'understandProject',
        label: 'I understand what this project does and how it is put together.',
        serves: 'C1',
      },
      {
        id: 'understandOrigins',
        label: 'I understand which piece of work in this project put which part of the dashboard there.',
        serves: 'C1',
      },
      {
        id: 'wouldNeedHelp',
        label: 'I would need someone to walk me through this project before I changed anything in it.',
        reverse: true,
        serves: 'C1',
      },
    ],
  },
  {
    id: 's2',
    heading: 'Stage 2',
    capMin: 4,
    optional: false,
    archetype: 'locate the piece of work behind a described defect',
    serves: "RQ2, claim C2; the reach answer is C3's prediction",
    title: {
      bikecount: 'Find the work behind the wrong number',
      footfall: 'Find the work behind the wrong number',
    },
    body: forEachProject(
      (w) => `
Run the command below first. It resets the project and prints the two numbers this stage is about, side by side.

    ./stage 2

**What happened:** ${w.publisher} published a ${w.document} last year saying that the average day in 2018 saw **${w.reported}** ${w.body}. The dashboard's by-year page now says **${w.dashboard}** for the same year. The numbers disagree because a colleague changed the way the dashboard works out an average. Days on the project's list of unusual days, such as ${w.unusualDays}, are now left out of every average, and the ${w.document} was written when every day still counted.

![The by-year page, with the 2018 row marked — its average-day number is the one that disagrees with the ${w.document}](${w.img.yearly})

The pages open on the most recent year. Set the date window at the top to cover 2018 — the year both numbers are about.

**Your job:** Find the piece of work in the project's history that made that change. You do not have to change any code.

**You are done when:** You can name the piece of work — a commit hash, a named piece of work, or an id all count. The questions after this stage ask you which one you found. If you are not certain, choose what you have and say that you are not certain. That is more useful to us than a guess.
`,
    ),
    tips: {
      git: [
        '`git log --oneline` lists the commits, newest first.',
        '`git show <hash>` shows what one commit changed.',
        '`git log --oneline -S "average"` finds the commits where a piece of text arrived or went away. Any word from the code works.',
        '`git log --oneline -- <file>` narrows that to one file, and `git blame <file>` says which commit last touched each line.',
      ],
      sgt: [
        '`sgt log` shows the history grouped by feature; `sgt log --rail` lists what happened, newest first.',
        '`sgt find "the bit that works out the averages"` searches by description. Any wording will do.',
        '`sgt log --focus "<name>"` opens one feature — or one ◆ piece of cross-feature work: the map stays, its chapters are listed underneath.',
        'To answer "which parts of the dashboard": `sgt show "<name>"` lists the files a feature — or a ◆ piece of cross-feature work — touches, and what taking it out would remove. `pages/<name>.py` is the page of the same name, and the workbench shows the same card.',
      ],
    },
    run: {
      script: { bikecount: './stage 2', footfall: './stage 2' },
      does: {
        bikecount: [
          'puts the project back to its full history, discarding anything from the last stage',
          'prints the number the report quotes next to the number the dashboard shows',
        ],
        footfall: [
          'puts the project back to its full history, discarding anything from the last stage',
          'prints the number the paper quotes next to the number the dashboard shows',
        ],
      },
    },
    scoredLocate: true,
    // The in-work text box (`identify`) is gone: it duplicated the recognition
    // question below, and typing a handle mid-stage measured transcription
    // under a clock rather than whether the work was found. The recognition
    // choice after the stage is the measure now. The answer key's `locate`
    // entries stay -- a facilitator can still score a name a participant says
    // aloud -- and the `identify` machinery in the renderer/key stays for any
    // future stage that wants a production measure.
    quiz: [
      // This is the recognition half, for the participant who found the work
      // but could not write down a handle for it. Unscored: promoting it means
      // adding `requestKeys.s2.choices.found` to the answer key.
      {
        kind: 'choice',
        id: 'found',
        prompt: 'Which of these is the work you found?',
        options: [
          { value: 'dateWindow', label: 'The work that made every page respect a picked date range.' },
          {
            value: 'eventDays',
            label: 'The work that started tracking unusual days and left them out of the averages.',
          },
          { value: 'yearTable', label: 'The work that added the by-year page the number appears on.' },
          { value: 'sides', label: 'The work that added the comparison between the two sensors.' },
          { value: 'notFound', label: 'I did not find it.' },
        ],
        scored: false,
      },
      {
        kind: 'behaviours',
        id: 'behaviours',
        prompt:
          'Which parts of the dashboard does that work affect? Tick the ones you would check if it were taken out.',
        scored: true,
      },
    ],
    quizConfidence: true,
    ratings: [
      // "I am confident I found the right piece of work" is gone: it asked the
      // same thing as the confidence rating directly above it, and pilots said
      // so.
      {
        id: 'understandWhy',
        label: 'I understand why my colleague made this change.',
        serves: 'C2',
      },
      {
        id: 'understandEffects',
        label: 'I understand what else in the project this change affects.',
        serves: 'C2',
      },
    ],
  },
  {
    id: 's3',
    heading: 'Stage 3',
    capMin: 4,
    optional: false,
    archetype: 'remove one piece of work that later work has landed on',
    serves: 'RQ3, claim C3',
    title: {
      bikecount: 'Take that work out',
      footfall: 'Take that work out',
    },
    body: forEachProject(
      (w) => `
Run the command below first. It resets the project and names the work you have to take out, so you have the name whether or not you found it in the last stage.

    ./stage 3

**What happened:** The committee never approved the change your colleague made. They want the averages to count every day the sensors recorded, including the unusual ones.

**Your job:** Take that work out of the project. Three things have to go: the list of unusual days the project keeps, the marks that flag those days on the daily and monthly charts, and the rule that leaves those days out of the averages. Everything else the dashboard shows has to keep working.

![The monthly page today: the coloured bars flag months containing an unusual day. After the removal, no bar is coloured and the note under the chart is gone.](${w.img.monthly})

Set the date window to 2018 while you check the pages — the marks only show when the window contains an unusual day, and the pages open on the most recent year.

**You are done when:** \`./check 3\` says the program still runs and the by-year page reads **${w.reported}** for 2018 again. Run it as often as you like. It prints the same words for everyone, it does not mark you, and a red line in it is information rather than a verdict.
`,
    ),
    tips: {
      git: [
        '`git revert <hash>` makes a new commit that undoes an old one. Give it the oldest of the three last.',
        'If it stops on a conflict, `git status` lists the unresolved files. Fix the marked lines and `git add` the file, or `git rm` a file the revert means to delete, then `git revert --continue`.',
        '`git revert --abort` walks away from a revert that has gone wrong and leaves nothing behind.',
        '`git log --oneline` and `git status` say where you are at any point.',
      ],
      sgt: [
        '`sgt revert "<name>"` shows you what the removal would do and changes nothing.',
        'Add `--yes` to actually do it: `sgt revert "<name>" --yes`.',
        'The name is the one `./stage 3` printed. `sgt log` names it under the map too, with the other work that spans features, and `sgt log --focus "<name>"` shows exactly what is in it.',
        '`sgt undo` reverses whatever you last did, and `sgt now` says where things stand.',
      ],
    },
    run: {
      script: { bikecount: './stage 3', footfall: './stage 3' },
      does: {
        bikecount: [
          'puts the project back to its full history, discarding anything from the last stage',
          'names the work to take out, in the words this setup uses for it',
        ],
        footfall: [
          'puts the project back to its full history, discarding anything from the last stage',
          'names the work to take out, in the words this setup uses for it',
        ],
      },
    },
    quiz: [
      {
        kind: 'behaviours',
        id: 'behaviours',
        prompt:
          'Which parts of the dashboard changed when the work came out? Tick the ones you saw change.',
        scored: true,
      },
      // "How did the removal go?" is gone. The three answers it could take --
      // applied cleanly, hit conflicts, edited by hand -- are all in the
      // telemetry already, in more detail and without asking the participant to
      // summarise four minutes of work into one line while the memory of it is
      // the thing the ratings below are measuring.
    ],
    quizConfidence: true,
    ratings: [
      {
        id: 'knewReach',
        label: 'Before I ran it, I knew what the removal was going to change.',
        serves: 'C3',
      },
      {
        id: 'matchedIntent',
        label: 'The result is what I intended.',
        serves: 'C3',
      },
      {
        id: 'worriedBroke',
        label: 'I was worried that I had broken something else.',
        reverse: true,
        serves: 'C3',
      },
    ],
  },
  {
    id: 's4',
    heading: 'Stage 4',
    capMin: 4,
    optional: false,
    archetype: 'put a removed piece of work back exactly',
    serves: 'RQ3, claim C3',
    title: {
      bikecount: 'Put it back',
      footfall: 'Put it back',
    },
    body: forEachProject(
      (w) => `
Run the command below first. It puts the project into the state where the work has already been taken out. Everyone starts stage 4 from this same state, whether or not their own removal in the last stage worked.

    ./stage 4

**What happened:** The committee has changed its mind. Now that they have seen the averages with every day counted, they agree with your colleague that ${w.ordinaryDay}, so those days should stay out of the averages after all.

**Your job:** Put that work back into the project, exactly as it was before the removal.

![The by-year page you are aiming for: the marked 2018 row reads its excluded-days number again](${w.img.yearly})

**You are done when:** \`./check 4\` says the program still runs and the by-year page reads **${w.dashboard}** for 2018 again.
`,
    ),
    tips: {
      git: [
        'The removal is three commits at the top of the history. `git log --oneline` shows them.',
        '`git revert <hash>` on a revert commit undoes the undoing.',
        'If it stops on a conflict, `git status` lists the unresolved files. Fix the marked lines and `git add` the file, or `git rm` a file the revert means to delete, then `git revert --continue`.',
        '`git show <hash>` reads any one of them if you want to see what it did.',
      ],
      sgt: [
        '`sgt restore "<name>" --yes` puts back what `sgt revert` took out. It takes the same name.',
        'Without `--yes` you get a preview and nothing happens.',
        '`sgt log` and `sgt now` say what the history records so far.',
        '`sgt undo` reverses whatever you last did.',
      ],
    },
    run: {
      script: { bikecount: './stage 4', footfall: './stage 4' },
      does: {
        bikecount: [
          'puts the project in the state where that work has already been taken out, the same for everyone',
        ],
        footfall: [
          'puts the project in the state where that work has already been taken out, the same for everyone',
        ],
      },
    },
    // No quiz. Both questions this stage used to ask -- how did you put it back,
    // and what convinced you it was back -- were self-reports of things already
    // recorded: the mechanism is in the telemetry, and what the participant
    // trusted is what the three rating statements below ask, one construct at a
    // time. The stage ends on the ratings.
    quiz: [],
    quizConfidence: false,
    ratings: [
      {
        id: 'backExact',
        label: 'The project is back exactly as it was before the removal.',
        serves: 'C3',
      },
      {
        id: 'historySays',
        label: "I could tell from the project's history that the work was back.",
        serves: 'C3',
      },
      {
        id: 'recheckByHand',
        label: 'I would want to re-check everything by hand before I trusted it.',
        reverse: true,
        serves: 'C3',
      },
    ],
  },
]

/**
 * Look up a stage, or undefined.
 *
 * Undefined is a real answer, not a defect. The experimenter dashboard renders
 * whatever request documents a participant's collection holds, and the pilots
 * ran earlier designs, so their collections still hold `d1` to `d4`, `r1` to
 * `r6`, `w1` to `w3`, `f1` and `f2`. Throwing here took the whole "Requests &
 * scoring" tab down mid-render whenever one was opened.
 */
export function requestById(id: RequestId): RequestSpec | undefined {
  return REQUESTS.find((r) => r.id === id)
}

/** What the participant calls one stage: "Stage 1". */
export function requestHeading(r: RequestSpec): string {
  return r.heading
}

/**
 * Minutes of capped work in a half. Summed rather than written down, because
 * it was written down before and a stage's cap could change without it.
 */
export const BLOCK_CAP_MIN = REQUESTS.reduce((sum, r) => sum + r.capMin, 0)

/**
 * Minutes a quiz-and-ratings pass is budgeted at, per stage. Untimed on
 * screen; this exists only so the schedule the participant reads includes the
 * answering, not just the work.
 */
export const QUIZ_EST_MIN = 1

/** What a task block costs in the schedule: the caps plus the answering. */
export const BLOCK_ESTIMATE_MIN = BLOCK_CAP_MIN + REQUESTS.length * QUIZ_EST_MIN

/**
 * Stages the participant is told to expect. Counted rather than written down:
 * an earlier design's preamble said "three requests, about twenty minutes"
 * and stayed that way through two redesigns of what was under it.
 */
export const STAGE_COUNT = REQUESTS.length
