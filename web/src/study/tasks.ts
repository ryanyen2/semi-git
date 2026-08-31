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
 * Ten, and the same ten on every checklist in the block.
 *
 * One list learned once, so the later checklists cost no reading and the
 * answers are directly comparable. The keys have to sit well inside it, so
 * neither "tick everything" nor "tick nothing" is close to right; the key
 * upload refuses a key naming zero or all ten (`answerKey.ts`).
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
      /** Scored as set F1 against the measured key in answer-key.json. */
      scored: true
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
  },
}

/** The same body text in both projects, with that project's words in it. */
function forEachProject(write: (w: (typeof PROJECT_WORDS)['bikecount']) => string): Record<Project, string> {
  return {
    bikecount: write(PROJECT_WORDS.bikecount).trim(),
    footfall: write(PROJECT_WORDS.footfall).trim(),
  }
}

const MECHANISM_REMOVE = [
  { value: 'clean', label: 'It applied cleanly in one step.' },
  { value: 'conflicts', label: 'I had to resolve conflicts along the way.' },
  { value: 'hand', label: 'I ended up editing files by hand.' },
  { value: 'unfinished', label: 'I did not finish.' },
]

const MECHANISM_RESTORE = [
  { value: 'undid', label: 'I undid the removal in one step.' },
  { value: 'history', label: 'I brought it back from the history another way.' },
  { value: 'hand', label: 'I re-made the change by hand.' },
  { value: 'unfinished', label: 'I did not finish.' },
]

// NOTE for the testbed build: stage 1's story below must match the recorded
// session that `./stage 1` replays, and the build gate in scripts/study/
// re-checks that the replayed change spans at least two files and contains
// exactly two distinguishable jobs (the requested one and one smaller
// unrequested fix). If the gate selects a different session, this wording is
// what changes. The quiz key for s1 is measured by rendering every page
// before and after the replay, never written by hand.
export const REQUESTS: RequestSpec[] = [
  {
    id: 's1',
    heading: 'Stage 1',
    capMin: 4,
    optional: false,
    archetype: 'read a multi-file assistant change and record it',
    serves: 'RQ1, claim C1',
    title: {
      bikecount: 'Record what the assistant did',
      footfall: 'Record what the assistant did',
    },
    body: forEachProject(
      (w) => `
Run the command below first. It puts the project into this stage's starting state.

    ./stage 1

**What happened:** Earlier today you asked the coding assistant to round the numbers on the dashboard's front page to the nearest ten, so that they stop implying ${w.precision}. The assistant has finished. Its changes are in your working copy, and none of them are recorded in the project's history yet.

**Your job:** Read what the assistant changed, in the editor or in the terminal, until you could describe it to a colleague. Then record all of it, the way this setup records finished work.

**You are done when:** Every one of the assistant's changes is recorded in the project's history, with a message in your own words, and nothing is left unrecorded.
`,
    ),
    tips: {
      git: [
        '`git status` lists the files that have changed but are not recorded yet.',
        '`git diff` shows what changed inside them.',
        '`git add <file>` then `git commit -m "your words"` records the change. `git add -A` stages everything at once.',
        'In the editor, the Source Control panel shows the same files and commits them.',
      ],
      // `git diff` is named in the sgt arm on purpose, and it is not a leak.
      //
      // Nothing is recorded yet at this point in the stage, and sgt has very
      // little to say about an unrecorded change: `sgt now` reports the whole
      // eleven-file replay as "1 edit(s) in 1 feature", and `sgt status` lists
      // seven of the eleven files. A participant told to read the change with
      // those alone is stuck, and being stuck is not the difference this study
      // is trying to measure -- the difference is what each setup RECORDS, one
      // line further down. Both arms have git, both stage bodies say "in the
      // editor or in the terminal", and the git arm's tips name the same two
      // reading commands.
      sgt: [
        '`sgt now` says where things stand.',
        '`git diff` shows the change line by line, and `git status` lists the files it touches. Nothing is recorded yet, so this is where the detail is.',
        'In the editor, the Changes view and the diff view show the same edits.',
        '`sgt save -m "your words"` records the change and prints which piece of work it went under.',
      ],
    },
    run: {
      script: { bikecount: './stage 1', footfall: './stage 1' },
      does: {
        bikecount: [
          "resets the project to this stage's starting state",
          "replays the assistant's changes into your working copy, unrecorded",
        ],
        footfall: [
          "resets the project to this stage's starting state",
          "replays the assistant's changes into your working copy, unrecorded",
        ],
      },
    },
    quiz: [
      {
        kind: 'behaviours',
        id: 'behaviours',
        prompt: "Which parts of the dashboard did the assistant's work change?",
        scored: true,
      },
      // Was a free-text box asking what else was in the same piece of work.
      // Pilots answered it with a shrug or a sentence about the diff, and it
      // cost a minute of an untimed quiz that people were already tired of. The
      // same thing as four options is answerable in five seconds and comparable
      // across participants.
      // Asks whether they READ where the record landed (the save echo / the commit in the log),
      // not what they did -- a participant who recorded without looking picks "I could not tell",
      // and that is the signal. The old wording ("What did it join?") assumed the sgt arm's
      // vocabulary and read as a riddle in the git arm.
      {
        kind: 'choice',
        id: 'joined',
        prompt:
          'When you recorded the work, did the setup connect it to any earlier work in the project?',
        options: [
          { value: 'alone', label: 'No — it stands on its own.' },
          { value: 'same', label: 'Yes — earlier work on the same part of the dashboard.' },
          { value: 'other', label: 'Yes — earlier work on a different part of the dashboard.' },
          { value: 'unsure', label: 'I could not tell.' },
        ],
        scored: false,
      },
    ],
    quizConfidence: true,
    ratings: [
      {
        id: 'understandChange',
        label: 'I understand the changes the assistant made to this codebase.',
        serves: 'C1',
      },
      {
        id: 'understandEffects',
        label: 'I understand the downstream effects of those changes on this codebase.',
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
        '`sgt intent list` prints every feature and checkpoint with the handle you can type back, and the groups that span several features at the bottom.',
        '`sgt show "<name>"` shows what one piece of work covers.',
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
        'The name is the one `./stage 3` printed. `sgt intent list` prints it too, at the bottom, with the groups that span several features.',
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
      {
        kind: 'choice',
        id: 'mechanism',
        prompt: 'How did the removal go?',
        options: MECHANISM_REMOVE,
        scored: false,
      },
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
    quiz: [
      {
        kind: 'choice',
        id: 'mechanism',
        prompt: 'How did you put it back?',
        options: MECHANISM_RESTORE,
        scored: false,
      },
      // Was "how would you convince a colleague, without showing them the
      // code". A good interview question and a bad quiz question: it asked for
      // a paragraph at the end of the block, and most pilots wrote one clause.
      {
        kind: 'choice',
        id: 'evidence',
        prompt: 'What convinced you that the work is back?',
        options: [
          { value: 'check', label: 'The check script said the number matched.' },
          { value: 'history', label: "The project's history says the work is back." },
          { value: 'pages', label: 'I opened the dashboard and looked at the pages.' },
          { value: 'code', label: 'I read the code and the files look right.' },
          { value: 'unsure', label: 'I am not sure that it is back.' },
        ],
        scored: false,
      },
    ],
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
