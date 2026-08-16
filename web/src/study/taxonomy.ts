// The nine-letter alphabet everything downstream reads.
//
// Raw telemetry is a mess of tool names, argv arrays and MCP verbs. The n-gram
// analysis, the process figure and the verification ratio all work on these
// categories instead, which is what makes a git session and an sgt session
// comparable at all: `git show` and `sgt show` are the same move.
//
// Defined in docs/study/protocol.md §6.

export const CATEGORIES = [
  'orient',
  'inspect',
  'search',
  'prompt',
  'agent_edit',
  'manual_edit',
  'history_op',
  'verify',
  'recover',
] as const

export type Category = (typeof CATEGORIES)[number]

export const CATEGORY_LABEL: Record<Category, string> = {
  orient: 'Orienting',
  inspect: 'Inspecting history',
  search: 'Searching code',
  prompt: 'Prompting the assistant',
  agent_edit: 'Assistant edits',
  manual_edit: 'Own edits',
  history_op: 'History operations',
  verify: 'Verifying',
  recover: 'Recovering',
}

export const CATEGORY_DESCRIPTION: Record<Category, string> = {
  orient: 'Broad reads with no particular target: log, status, now.',
  inspect: 'Targeted reads: show, blame, diff of a named thing, why, recall.',
  search: 'Looking through the code itself rather than its history.',
  prompt: 'A message sent to the assistant.',
  agent_edit: 'A file written by the assistant.',
  manual_edit: 'A file changed with no assistant edit accounting for it. Inferred.',
  history_op: 'Anything that changes what the history says.',
  verify: 'Running tests, running the app, or diffing after a change.',
  recover: 'Getting back to a good state after something went wrong.',
}

// Ordered so the process figure reads left to right as understanding, then
// acting, then checking.
export const CATEGORY_ORDER: Category[] = [
  'orient',
  'inspect',
  'search',
  'prompt',
  'agent_edit',
  'manual_edit',
  'history_op',
  'verify',
  'recover',
]

export interface ClassifyContext {
  /**
   * Has anything been edited or operated on since the last verification? A
   * `git diff` before a change is orientation; the same command after one is
   * checking. Ignoring position mislabels roughly a third of diffs, which was
   * visible in both pilot logs.
   */
  dirtySinceCheck: boolean
  /** Did the immediately preceding history operation fail? Then a restore is recovery. */
  lastOpFailed: boolean
}

export interface ClassifiableEvent {
  kind: string
  name: string | null
  text: string | null
  ok?: boolean | null
  exitCode?: number | null
}

const GIT_ORIENT = /^(log|status|shortlog|branch|tag|remote)$/
const GIT_INSPECT = /^(show|blame|diff|bisect|annotate|whatchanged|describe|rev-list|rev-parse)$/
const GIT_HISTORY = /^(revert|reset|rebase|cherry-pick|commit|merge|checkout|switch|restore|stash|am|filter-branch|filter-repo|rm|mv|add)$/
const GIT_RECOVER = /^(reflog|fsck)$/

const SGT_ORIENT = /^(now|log|status)$/
const SGT_INSPECT = /^(show|why|recall|diff|explain|blame|feature)$/
const SGT_HISTORY = /^(save|revert|restore|split|merge|split-op|merge-op|transplant|rename|move|land|commit|propose|push|plan|checkpoint|adopt|retire|kill)$/
const SGT_VERIFY = /^(drift|fsck|oracle)$/

const VERIFY_CMD = /^(pytest|py\.test|tox|nox|make|npm|pnpm|yarn|cargo|go)$/
const SEARCH_CMD = /^(grep|rg|ag|ack|find|fd|ls|cat|head|tail|less|more|bat|tree|wc|jq|sed|awk)$/
const NOISE_CMD = /^(cd|export|echo|clear|pwd|which|env|source|history|exit|true|:|set|unset|alias)$/

/** argv-ish split that keeps quoted strings together well enough for a verb. */
function words(text: string | null): string[] {
  if (!text) return []
  return text.trim().split(/\s+/).filter(Boolean)
}

/** First token that is not a flag, starting at `from`. */
function subcommand(parts: string[], from: number): string {
  for (let i = from; i < parts.length; i++) {
    if (!parts[i].startsWith('-')) return parts[i]
  }
  return ''
}

function classifyShell(text: string | null, ctx: ClassifyContext): Category | null {
  const parts = words(text)
  if (parts.length === 0) return null

  // Take the last stage of a pipeline: `git log | head` is still a git log.
  const bin = parts[0].replace(/^.*\//, '')

  if (bin === 'git') {
    const sub = subcommand(parts, 1)
    const rest = parts.slice(1).join(' ')
    if (GIT_RECOVER.test(sub)) return 'recover'
    if (sub === 'reset' && /--hard/.test(rest)) return 'recover'
    if (sub === 'checkout' && /(^|\s)--(\s|$)/.test(rest)) return 'recover'
    if (sub === 'diff' || sub === 'status') return ctx.dirtySinceCheck ? 'verify' : 'orient'
    if (GIT_HISTORY.test(sub)) return 'history_op'
    if (GIT_INSPECT.test(sub)) return 'inspect'
    if (GIT_ORIENT.test(sub)) return 'orient'
    return 'inspect'
  }

  if (bin === 'sgt') {
    const sub = subcommand(parts, 1)
    const rest = parts.slice(1).join(' ')
    if (sub === 'restore' && ctx.lastOpFailed) return 'recover'
    if (SGT_VERIFY.test(sub)) return 'verify'
    if (SGT_HISTORY.test(sub)) return 'history_op'
    if (SGT_INSPECT.test(sub)) return 'inspect'
    if (SGT_ORIENT.test(sub)) return /--refresh/.test(rest) ? 'orient' : 'orient'
    return 'inspect'
  }

  if (VERIFY_CMD.test(bin)) return 'verify'
  if (bin === 'python' || bin === 'python3' || bin === 'uv') {
    if (/pytest|unittest/.test(text ?? '')) return 'verify'
    // Running the application itself is verification too.
    if (/-m\s+\w+|\.py\b/.test(text ?? '')) return 'verify'
    return 'verify'
  }
  if (SEARCH_CMD.test(bin)) return 'search'
  if (NOISE_CMD.test(bin)) return null
  return null
}

/**
 * Category for one event, or null for events that carry no analytic signal
 * (`cd`, `clear`, heartbeats). Nulls are dropped before n-gram analysis rather
 * than folded into an "other" bucket, because a filler symbol in the alphabet
 * dominates every bigram it appears in.
 */
export function classify(ev: ClassifiableEvent, ctx: ClassifyContext): Category | null {
  switch (ev.kind) {
    case 'prompt':
      return 'prompt'
    case 'heartbeat':
    case 'marker':
    case 'session':
    case 'repo':
      return null
    case 'command':
      return classifyShell(ev.text ?? ev.name, ctx)
    case 'tool': {
      const tool = ev.name ?? ''
      if (/^(Edit|Write|NotebookEdit|MultiEdit)$/.test(tool)) return 'agent_edit'
      if (/^(Read|Grep|Glob|WebFetch|WebSearch)$/.test(tool)) return 'search'
      if (tool === 'Bash' || tool === 'BashOutput') return classifyShell(ev.text, ctx)
      if (tool.startsWith('mcp__sgt__')) {
        const verb = tool.replace('mcp__sgt__sgt_', '').replace('mcp__sgt__', '')
        if (SGT_VERIFY.test(verb)) return 'verify'
        if (SGT_HISTORY.test(verb)) return 'history_op'
        if (SGT_INSPECT.test(verb)) return 'inspect'
        if (SGT_ORIENT.test(verb)) return 'orient'
        return 'inspect'
      }
      if (tool.startsWith('mcp__')) return 'inspect'
      return null
    }
    default:
      return null
  }
}

/** Does this category leave the working tree or the history changed? */
export function isMutating(c: Category): boolean {
  return c === 'agent_edit' || c === 'manual_edit' || c === 'history_op'
}

// ---------------------------------------------------------------------------
// Prompt specificity
// ---------------------------------------------------------------------------

export type Specificity = 0 | 1 | 2 | 3

export const SPECIFICITY_LABEL: Record<Specificity, string> = {
  0: 'No target named',
  1: 'Named a file or test',
  2: 'Named a behavior or feature',
  3: 'Named a commit or intent',
}

const SHA = /\b[0-9a-f]{7,40}\b/
const INTENT_ID = /\bf-[0-9a-f]{6,}(@\d+)?\b/
const PATHISH = /\b[\w./-]+\.(py|md|json|txt|toml|cfg|yaml|yml)\b/
const TESTISH = /\btest_\w+|\btests?\/\S+/

/**
 * How precisely a prompt points at something. The concrete form of "I could
 * point the assistant at exactly the part of the history I meant", and the
 * measure most likely to show the representational difference: sgt gives people
 * nameable things to point at, and you can see whether they use them.
 */
export function promptSpecificity(text: string): Specificity {
  const t = text.toLowerCase()
  if (INTENT_ID.test(t) || SHA.test(t)) return 3
  if (/\b(feature|intent|checkpoint|commit|episode|change)\b/.test(t) && /\b(the|that)\b/.test(t)) {
    if (PATHISH.test(t) || TESTISH.test(t)) return 2
    return 2
  }
  if (PATHISH.test(t) || TESTISH.test(t)) return 1
  return 0
}
