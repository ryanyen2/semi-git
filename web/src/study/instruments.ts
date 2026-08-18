// Every questionnaire the study administers, with wording fixed.
//
// Wording lives in code, not in a spreadsheet, because the paper has to report
// it and because an instrument edited between participants is an instrument
// with no scale. Each battery carries a version; if an item ever changes, the
// version changes with it and the old responses stay interpretable.
//
// Source of truth for the design decisions here: docs/study/protocol.md §5.

export type ItemType =
  | 'checkbox'
  | 'text'
  | 'textarea'
  | 'number'
  | 'likert'
  | 'slider'
  | 'select'
  | 'multi'
  | 'grid'
  | 'tlx'
  | 'statement'
  /** A block heading inside a questionnaire. Carries no answer. */
  | 'section'

export interface Option {
  value: string
  label: string
}

export interface GridRow {
  id: string
  label: string
}

export interface Item {
  id: string
  type: ItemType
  label: string
  /** Compact label used on figure axes. Likert items only. */
  shortLabel?: string
  help?: string
  required?: boolean
  options?: Option[]
  rows?: GridRow[]
  min?: number
  max?: number
  step?: number
  /** Left and right anchor text for scales. */
  anchors?: [string, string]
  /** Scored in the opposite direction when totalled. */
  reverse?: boolean
  placeholder?: string
  /** Which claim or RQ this item serves. Shown in the dashboard, not to the participant. */
  serves?: string
  /**
   * Collected here for convenience, but not part of this instrument's construct.
   *
   * A manipulation check rides along on the questionnaire that happens to be in
   * the right place in the flow; it is not one of the things that questionnaire
   * measures. Anything that averages an instrument or plots it as a block has
   * to leave these out, or it reports a check as if it were a finding -- and in
   * the case of a five-point check inside a block of seven-point items, plots it
   * on the wrong axis while doing so.
   */
  check?: boolean
}

export interface Instrument {
  id: string
  version: string
  title: string
  intro?: string
  items: Item[]
  estimateMin: number
  /** Repeated once per half rather than once per session. */
  perHalf: boolean
}

// ---------------------------------------------------------------------------
// Consent
// ---------------------------------------------------------------------------

export const CONSENT: Instrument = {
  id: 'consent',
  version: 'consent-v1',
  title: 'Consent',
  perHalf: false,
  estimateMin: 3,
  intro:
    'Please read each line and tick it if you agree. The first five are needed to take part. The last one is genuinely optional and ticking or not ticking it makes no difference to anything else.',
  items: [
    {
      id: 'read',
      type: 'checkbox',
      required: true,
      label: 'I have read the information sheet and had my questions answered.',
    },
    {
      id: 'recording',
      type: 'checkbox',
      required: true,
      label: 'I agree to my screen and voice being recorded for this session.',
    },
    {
      id: 'telemetry',
      type: 'checkbox',
      required: true,
      label:
        'I agree that the commands I run and the messages I send to the AI assistant during the session are recorded.',
    },
    {
      id: 'deidentified',
      type: 'checkbox',
      required: true,
      label: 'I understand my data will be de-identified and reported in aggregate.',
    },
    {
      id: 'withdraw',
      type: 'checkbox',
      required: true,
      label: 'I understand I can stop at any time, without giving a reason, and still be paid.',
    },
    {
      id: 'quotes',
      type: 'checkbox',
      required: false,
      label:
        'Optional: I agree to short anonymized quotes from my session appearing in a publication.',
    },
    {
      id: 'name',
      type: 'text',
      required: true,
      label: 'Type your name to sign',
      placeholder: 'Your name',
      help: 'Stored with your consent record and separated from your task data before analysis.',
    },
  ],
}

// ---------------------------------------------------------------------------
// Background
// ---------------------------------------------------------------------------

const FREQ: Option[] = [
  { value: '0', label: 'Never' },
  { value: '1', label: 'Rarely' },
  { value: '2', label: 'Sometimes' },
  { value: '3', label: 'Often' },
]

export const BACKGROUND: Instrument = {
  id: 'background',
  version: 'background-v1',
  title: 'About you',
  perHalf: false,
  estimateMin: 5,
  intro:
    'None of this is a test and none of it affects what you are asked to do. It lets us describe who took part, and it lets us tell apart differences caused by the tools from differences caused by experience.',
  items: [
    {
      id: 'yearsCoding',
      type: 'number',
      required: true,
      label: 'Roughly how many years have you been writing code seriously?',
      min: 0,
      max: 50,
    },
    {
      id: 'yearsGit',
      type: 'number',
      required: true,
      label: 'Roughly how many years have you used git?',
      min: 0,
      max: 30,
    },
    {
      id: 'gitVerbs',
      type: 'grid',
      required: true,
      label: 'How often do you use each of these?',
      help: 'There is no right answer here. Plenty of good engineers have never run bisect.',
      options: FREQ,
      serves: 'git expertise composite, 0-24',
      rows: [
        { id: 'log', label: 'git log' },
        { id: 'blame', label: 'git blame' },
        { id: 'bisect', label: 'git bisect' },
        { id: 'revert', label: 'git revert' },
        { id: 'reset', label: 'git reset' },
        { id: 'rebasei', label: 'git rebase -i' },
        { id: 'reflog', label: 'git reflog' },
        { id: 'cherrypick', label: 'git cherry-pick' },
      ],
    },
    {
      id: 'agentTools',
      type: 'multi',
      required: true,
      label: 'Which of these have you used in agent mode, where the tool edits files itself?',
      options: [
        { value: 'claude-code', label: 'Claude Code' },
        { value: 'cursor', label: 'Cursor' },
        { value: 'copilot', label: 'GitHub Copilot agent mode' },
        { value: 'codex', label: 'Codex' },
        { value: 'windsurf', label: 'Windsurf' },
        { value: 'other', label: 'Something else' },
        { value: 'none', label: 'None of these' },
      ],
    },
    {
      id: 'agentFrequency',
      type: 'select',
      required: true,
      label: 'How often do you work with an AI coding assistant?',
      options: [
        { value: 'daily', label: 'Most days' },
        { value: 'weekly', label: 'Most weeks' },
        { value: 'monthly', label: 'Some months' },
        { value: 'rarely', label: 'Rarely' },
        { value: 'never', label: 'Never' },
      ],
    },
    {
      id: 'aiShare',
      type: 'slider',
      required: true,
      label: 'Of the code you shipped last month, roughly what share did an assistant write?',
      min: 0,
      max: 100,
      step: 5,
      anchors: ['None of it', 'All of it'],
    },
    {
      id: 'languages',
      type: 'text',
      required: true,
      label: 'Which languages do you work in most?',
      placeholder: 'Python, TypeScript, ...',
    },
    {
      id: 'priorSgt',
      type: 'select',
      required: true,
      label: 'Have you used a tool called sgt or semi-git before?',
      help: 'If yes, say so. It does not disqualify you from anything, it just has to be recorded.',
      options: [
        { value: 'no', label: 'No' },
        { value: 'heard', label: 'Heard of it, never used it' },
        { value: 'yes', label: 'Yes, I have used it' },
      ],
    },
  ],
}

// ---------------------------------------------------------------------------
// NASA-TLX, raw
// ---------------------------------------------------------------------------

const tlxItem = (
  id: string,
  label: string,
  help: string,
  anchors: [string, string],
  reverse = false,
): Item => ({
  id,
  type: 'tlx',
  label,
  help,
  anchors,
  reverse,
  required: true,
  min: 0,
  max: 100,
  step: 5,
})

export const TLX: Instrument = {
  id: 'tlx',
  version: 'tlx-v3',
  title: 'How that felt',
  perHalf: true,
  estimateMin: 2,
  // Raw TLX: six subscales, unweighted, on the instrument's own 21-point scale.
  // Not on the seven points the rest of this questionnaire uses -- a coarser
  // scale does not blur TLX, it changes its shape, moving frustration onto the
  // physical subscale and splitting effort across two.
  //
  // The block names the requests rather than "the half you just finished",
  // because TLX measures the load of a bounded task and returns something else
  // when pointed at an hour of mixed activity.
  intro:
    'Six scales about the requests you just worked through. Click anywhere on each line. Answer quickly, first instinct. There is no good or bad score.',
  items: [
    // Each subscale carries its published name, its question and its published
    // definition. The name alone is not enough: the six correlate strongly in
    // interactive work, and a participant reading only "Mental demand" answers
    // several of them alike. Name and question are joined by an em dash, which
    // is where the renderer splits them.
    tlxItem(
      'mental',
      'Mental demand — How mentally demanding was the task?',
      'How much mental and perceptual activity was required? Thinking, deciding, looking, ' +
        'remembering, searching. Was the task easy or demanding, simple or complex?',
      ['Very low', 'Very high'],
    ),
    tlxItem(
      'physical',
      'Physical demand — How physically demanding was the task?',
      // No "for desk work this is usually low, and that is a normal answer" here
      // any more. It was meant kindly and it told the participant what to
      // answer before they answered, on the one subscale where a floor effect
      // is the expected result -- so it manufactured the very reading it was
      // reassuring them about.
      'How much physical activity was required? Typing, clicking, moving between windows. Was ' +
        'the task easy or demanding, restful or laborious?',
      ['Very low', 'Very high'],
    ),
    tlxItem(
      'temporal',
      'Temporal demand — How hurried or rushed was the pace of the task?',
      'How much time pressure did you feel because of the rate at which things happened? Was the ' +
        'pace slow and leisurely, or rapid and frantic?',
      ['Very low', 'Very high'],
    ),
    // Presented failure-to-perfect, the direction the words are read in, and
    // reversed exactly once in `tlxScore`. It was previously presented
    // perfect-to-failure AND reversed in scoring, so a participant who felt
    // they had done perfectly contributed the maximum possible workload.
    tlxItem(
      'performance',
      'Performance — How successful were you in accomplishing what you were asked to do?',
      'How satisfied were you with how you did? This is the one scale whose ends run the other ' +
        'way, so read them.',
      ['Failure', 'Perfect'],
      true,
    ),
    tlxItem(
      'effort',
      'Effort — How hard did you have to work to accomplish your level of performance?',
      'Mentally and physically, how hard did you have to work to reach the level of performance ' +
        'you reached?',
      ['Very low', 'Very high'],
    ),
    tlxItem(
      'frustration',
      'Frustration — How insecure, discouraged, irritated, stressed and annoyed were you?',
      'As against secure, content, relaxed and complacent.',
      ['Very low', 'Very high'],
    ),
  ],
}

// ---------------------------------------------------------------------------
// UMUX-Lite
// ---------------------------------------------------------------------------
//
// Two items, on their published seven points, replacing the ten-item SUS.
//
// The referent is filled in as "the setup you just used" rather than left as
// "this system". Both halves run the same assistant in the same editor on the
// same kind of project; an unqualified "system" would have been answered about
// different objects by different participants, and the difference between the
// halves is the entire measurement.
//
// Reported raw on 0-100 by the published formula. Deliberately NOT converted to
// a SUS-equivalent: that regression was fitted to particular corpora, and a
// within-participant difference gains nothing from the transformation while
// inheriting its error.

const umuxItem = (id: string, label: string, serves: string): Item => ({
  id,
  type: 'likert',
  label,
  serves,
  required: true,
  min: 1,
  max: 7,
  anchors: ['Strongly disagree', 'Strongly agree'],
})

export const UMUX_LITE: Instrument = {
  id: 'umux',
  version: 'umux-lite-v1',
  title: 'This setup',
  perHalf: true,
  estimateMin: 1,
  intro:
    'Two statements about the setup you just used for those requests. Rate how much you agree.',
  items: [
    umuxItem(
      'capability',
      "This setup's capabilities meet my requirements.",
      'usability / capability',
    ),
    umuxItem('easy', 'This setup is easy to use.', 'usability / ease'),
  ],
}

// ---------------------------------------------------------------------------
// History legibility and agent collaboration. This is Figure 1.
// ---------------------------------------------------------------------------

const hlacItem = (
  id: string,
  shortLabel: string,
  label: string,
  serves: string,
  reverse = false,
): Item => ({
  id,
  type: 'likert',
  label,
  shortLabel,
  serves,
  reverse,
  required: true,
  min: 1,
  max: 7,
  anchors: ['Strongly disagree', 'Strongly agree'],
})

const section = (id: string, label: string): Item => ({
  id, type: 'section', label, required: false,
})

export const HLAC: Instrument = {
  id: 'hlac',
  version: 'hlac-v3',
  title: 'Working with this project history',
  perHalf: true,
  estimateMin: 3,
  // Likert-TYPE items grouped into ad-hoc composites, not a validated scale.
  // Reported item by item, with the block mean as a summary rather than as a
  // construct score, and with no internal-consistency coefficient: at three to
  // four items and this sample size, a coefficient would lend an ad-hoc block
  // the appearance of a validated one.
  //
  // Grouped under headings in a fixed order rather than randomized. An
  // undifferentiated column of near-identical rows gets answered by pattern;
  // headings are the cheapest guard against that, and they only work if the
  // items that belong together sit together. The guard against straight-lining
  // is the reverse-keyed items instead.
  intro:
    'Statements about the requests you just worked through. Think about what it was actually like, not what you think it should have been like.',
  items: [
    section('secFind', 'Finding your way around'),
    hlacItem('q1', 'Found when it changed', 'I could find when a behavior changed.', 'C1'),
    hlacItem('q2', 'Found why it changed', 'I could find out why a change was made.', 'C1'),
    hlacItem(
      'q3',
      'Saw the whole piece of work',
      'When I found a change, I could see what larger piece of work it belonged to.',
      'C1',
    ),
    hlacItem(
      'q11',
      'Guessed at names',
      'I had to guess at names or ids to find what I was looking for.',
      'C1',
      true,
    ),

    section('secChange', 'Changing things'),
    hlacItem(
      'q4',
      'Knew what else it would touch',
      'Before I changed anything, I could tell what else would be affected.',
      'C2',
    ),
    hlacItem(
      'q5',
      'Removed a feature safely',
      'I could take a feature out without worrying about breaking what came after it.',
      'C2',
    ),
    hlacItem(
      'q6',
      'Recovered from mistakes',
      'When something went wrong, I could get back to a good state.',
      'C2',
    ),
    hlacItem(
      'q12',
      'Surprised by the result',
      'A change did something I had not expected.',
      'C2',
      true,
    ),

    section('secPicture', 'What you came away with'),
    hlacItem(
      'q7',
      'Clear picture of the project',
      'I ended up with a clear picture of how this project got to where it is.',
      'C3',
    ),
    hlacItem(
      'q13',
      'Would get back up to speed',
      'If I came back to this project in a month, what is recorded would get me back up to speed.',
      'C3',
    ),

    section('secAgent', 'Working with the assistant'),
    hlacItem(
      'q8',
      'Directed the assistant precisely',
      'I could point the assistant at exactly the part of the history I meant.',
      'Q4',
    ),
    hlacItem(
      'q9',
      "Checked the assistant's work",
      'I could check what the assistant did against what I asked for.',
      'Q4',
    ),
    // An honesty valve. If a condition wins understanding, control and this,
    // suspect acquiescence; if it wins the others while losing this, the story
    // is "a cost paid knowingly" and the data reads as credible.
    hlacItem(
      'q14',
      'Accepted unreviewed work',
      'I accepted changes from the assistant that I had not really reviewed.',
      'Q4',
      true,
    ),
    hlacItem(
      'q10',
      'Fought the tool',
      'I spent effort fighting the tool rather than doing the task.',
      'straight-lining guard',
      true,
    ),

    // Two manipulation checks, not opinions about the setup.
    //
    // The paper claims the two projects are isomorphic and that the requests
    // are the kind of thing that happens in real work. Both claims are
    // currently made by construction and defended by argument, which is the
    // first thing a reviewer pushes on. Two items per half turn each into
    // something measured, and both have precedent: the closest two studies of
    // history tools each ran one.
    //
    // The time-pressure item matters more than it looks. Every request here is
    // capped, so "the cap bound harder in one condition" is a live alternative
    // explanation for any difference in what people got done. Asked this way it
    // becomes a number that can be checked rather than a threat to be argued
    // away in the discussion.
    section('secChecks', 'About the requests themselves'),
    {
      id: 'realistic',
      type: 'likert',
      required: true,
      check: true,
      min: 1,
      max: 5,
      shortLabel: 'Requests were realistic',
      serves: 'manipulation check — task realism',
      anchors: ['Strongly disagree', 'Strongly agree'],
      label:
        'These requests were realistic. I can see this situation happening in real development.',
    },
    {
      id: 'timePressure',
      type: 'select',
      required: true,
      check: true,
      serves: 'manipulation check — did the cap bind',
      label: 'How much time pressure did you feel?',
      help: 'About the clock specifically, not about how hard the work was.',
      options: [
        { value: '1', label: 'Too much. I could not cope, regardless of difficulty' },
        { value: '2', label: 'A fair amount. I could have done better with more time' },
        { value: '3', label: 'Not much. I had to hurry a bit, but it was fine' },
        { value: '4', label: 'Very little. I was quite comfortable with the time' },
        { value: '5', label: 'None at all' },
      ],
    },
  ],
}

// The five-question recall quiz and the three-minute "tell the story" summary
// used to sit here, one of each per half. Both are gone.
//
// They cost twelve minutes a session and asked the participant to write, from
// memory, with the project closed, immediately after a block they had usually
// just run out of time on. What came back was short, hedged and graded by hand
// against a rubric, and the two conditions differed less on it than the graders
// differed from each other. The measure they were meant to support -- what a
// person carries away from a history -- is still there in the HLAC block's
// "what you came away with" items, which cost thirty seconds and no writing.
//
// Removing them also removes the only reason the answer key had to ship a
// `quizAnswers` block, and the only step that had to be locked after
// submission.

// ---------------------------------------------------------------------------
// Preference and close
// ---------------------------------------------------------------------------

// Five points, not three. The midpoint stays -- "these were the same here" is a
// real answer and a block that cannot record it cannot describe a tradeoff --
// but at twelve participants the distance between "leaned that way" and "chose
// that one" is most of the result, and a three-option select throws it away.
//
// Symmetric on purpose. The comparable published instruments all name the new
// tool in the stem ("I found Gitless to be easier to use than Git"), which
// anchors on it and leaves disagreement ambiguous between "the other one won"
// and "no difference". Neither setup is named first here.
//
// Analysed by recoding to -2..+2 in the sgt-positive direction, whichever
// letter sgt was for that participant, and reporting per item with its own n.
// The midpoint is a substantive category, never dropped as missing -- deciding
// that after seeing the data would be a forking path.
const prefOptions: Option[] = [
  { value: 'A2', label: 'A, clearly' },
  { value: 'A1', label: 'A, slightly' },
  { value: 'none', label: 'No real difference' },
  { value: 'B1', label: 'B, slightly' },
  { value: 'B2', label: 'B, clearly' },
]

const pref = (id: string, label: string, serves?: string): Item => ({
  id, type: 'select', label, options: prefOptions, serves, required: true,
})

export const PREFERENCE: Instrument = {
  id: 'preference',
  version: 'preference-v2',
  title: 'Comparing the two setups',
  perHalf: false,
  estimateMin: 5,
  // Rewritten shorter, and closed.
  //
  // The v1 block ran eighteen items, five of which were required free-text
  // "Why?" boxes sitting under a forced choice. It was the last thing in a
  // two-hour session, and it read like it: by the third box the answers were
  // "same reason as above". The reasons are now one multi-select, which is the
  // thing those boxes were being mined for anyway, and the one open box left is
  // optional.
  //
  // The choices name jobs the participant actually did across the two halves, in
  // outcome terms, never in tool terms -- "taking one piece of work out
  // without breaking the rest", not "reverting a feature". A question phrased as
  // a mechanism only one setup has is not a comparison, it is a leading
  // question with a forced answer.
  //
  // "No real difference" is offered on every one and stays a real answer. The
  // paper's argument is about a tradeoff, and a block that cannot record "these
  // were the same here" cannot describe one.
  intro:
    'Now that you have used both, which one would you rather have had for each kind of job? There is no expected answer, and "no real difference" is a real answer — some of these jobs may genuinely not differ, and we would rather have that than a guess.',
  items: [
    // Which letter was which, on the page, at the moment they are asked to
    // compare them. Every comparable published study named the two tools
    // outright; we cannot, because naming them tells the participant which one
    // is ours. The cost of that choice is that "Setup A" is an arbitrary token
    // they last saw an hour ago, and this line is what we pay instead.
    //
    // Note for the writeup: A and B are assigned by order, so letter and order
    // are perfectly confounded within a participant and recency favours B.
    // Counterbalancing fixes that across the group, not inside one person.
    {
      id: 'reminder',
      type: 'statement',
      required: false,
      label: 'Setup A was the one you used first. Setup B was the one you used second.',
    },
    section('secJobs', 'The jobs you just did'),
    pref('jobWhatChanged', 'Working out when a behavior changed, and what caused it', 'C1'),
    pref('jobWhatCameWith', 'Working out what else came along with a change', 'C1'),
    pref('jobRemove', 'Taking one piece of work out without breaking the rest', 'C2'),
    pref('jobPutBack', 'Putting back part of what you took out, after the fact', 'C2'),
    pref('jobIntended', 'Being confident the result was what you intended', 'C2'),
    pref('jobRecover', 'Getting back to a good state when something went wrong', 'C2'),
    // Verification only. Directing the assistant is near-identical across the two
    // arms -- same assistant, same prompts -- so half of the old wording was
    // noise, and the two halves are known to come apart: in the closest
    // published comparison 23 of 24 people called one tool more helpful while
    // only 10 of 24 trusted its output more. The HLAC block already splits them
    // into two items, so collapsing them here disagreed with our own battery.
    pref('jobAgent', 'Checking what the AI assistant had actually done', 'Q4'),

    {
      id: 'reasons',
      type: 'multi',
      required: true,
      label: 'What made the difference, wherever you felt one?',
      help: 'Tick anything that applies. If nothing does, tick the last one.',
      // Replaces five free-text "Why?" boxes. The options are the reasons
      // pilots actually gave, in their words, plus the two that would be
      // evidence against us: knowing the commands already, and not trusting
      // what the tool did. An option list with no losing options is a leading
      // question wearing a checkbox.
      options: [
        { value: 'preview', label: 'I could see what a change would do before doing it' },
        { value: 'names', label: 'What things were called matched what I was looking for' },
        { value: 'blast', label: 'I could tell what else would be affected' },
        { value: 'whole', label: 'I could see the whole piece of work, not just the lines' },
        { value: 'undoEasy', label: 'Undoing was easy' },
        { value: 'undoTrust', label: 'I trusted what the undo had done' },
        { value: 'check', label: "I could check the assistant's work against what I asked" },
        { value: 'familiar', label: 'I already knew the commands, so I was faster' },
        { value: 'predictable', label: 'I could predict exactly what it would do' },
        { value: 'escape', label: 'When it went wrong I knew how to get out' },
        { value: 'none', label: 'Nothing much — they felt about the same' },
      ],
    },

    section('secWhere', 'Where each one would earn its keep'),
    // Two hypotheticals, pointing opposite ways: a repository you will never see
    // again is not a job that rewards reading history carefully, and a codebase
    // you will own for a year is. A participant who picks the same setup for
    // both, having also picked it for all seven jobs above, is evidence of
    // demand characteristics and is reported as such. Differentiated answers
    // are the credible signal.
    //
    // A third, "a production hotfix under time pressure", was cut. Its own note
    // recorded that we had no prediction for it, and an item nobody can be wrong
    // about cannot discriminate -- it was costing a question to collect a shrug.
    //
    // These are the weakest items in the block and are kept deliberately few:
    // each asks a person to forecast from thirty-five minutes of use, which the
    // seven items above do not.
    pref(
      'scenarioThrowaway',
      'A repository you have never seen and will not see again',
      'discriminant — plain git expected',
    ),
    pref(
      'scenarioOwn',
      'A codebase you will own for the next year',
      'discriminant — sgt expected',
    ),
    section('secOverall', 'Overall'),
    pref('overall', 'Which setup would you rather work in?'),
    {
      id: 'wouldUseA',
      type: 'likert',
      required: true,
      min: 1,
      max: 7,
      anchors: ['Strongly disagree', 'Strongly agree'],
      label: 'I would want Setup A on my own projects.',
    },
    {
      id: 'wouldUseB',
      type: 'likert',
      required: true,
      min: 1,
      max: 7,
      anchors: ['Strongly disagree', 'Strongly agree'],
      label: 'I would want Setup B on my own projects.',
    },
    {
      id: 'cost',
      type: 'multi',
      required: true,
      label: 'What would put you off using the one you preferred?',
      help: 'The honest answer here is worth more to us than the one above.',
      // Every tool study collects reasons to adopt. This one collects the price,
      // because the finding is a tradeoff and a tradeoff with no cost recorded
      // reads as advocacy.
      options: [
        { value: 'learn', label: 'Learning it' },
        { value: 'trust', label: 'Not being sure what it had done' },
        { value: 'slow', label: 'Waiting for it' },
        { value: 'wrong', label: 'It grouped or named things in ways I disagreed with' },
        { value: 'reconstruct', label: 'Having to piece together what happened from the messages' },
        { value: 'team', label: 'Everyone else uses the other one' },
        { value: 'escape', label: 'Not knowing how to get out when it went wrong' },
        { value: 'nothing', label: 'Nothing — I would use it tomorrow' },
      ],
    },
    {
      id: 'missing',
      type: 'textarea',
      required: false,
      label: 'Anything you wanted to ask the project history and could not?',
      help: 'Optional. Answer from what you remember wanting, not from what either setup offered.',
    },
  ],
}

export const ALL_INSTRUMENTS: Instrument[] = [
  CONSENT,
  BACKGROUND,
  TLX,
  UMUX_LITE,
  HLAC,
  PREFERENCE,
]

export function instrumentById(id: string): Instrument | undefined {
  return ALL_INSTRUMENTS.find((i) => i.id === id)
}

/** Response doc id. Per-half instruments get one doc per half. */
export function responseDocId(instrumentId: string, half: number | null): string {
  return half ? `${instrumentId}-h${half}` : instrumentId
}
