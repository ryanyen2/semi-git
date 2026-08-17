import { describe, expect, it } from 'vitest'
import { classify, type ClassifiableEvent, type ClassifyContext } from '../src/study/taxonomy'

const clean: ClassifyContext = { dirtySinceCheck: false, lastOpFailed: false }

function cat(text: string, name = 'sgt', ctx: ClassifyContext = clean) {
  return classify({ kind: 'command', name, text } as ClassifiableEvent, ctx)
}

/**
 * These are real command lines, copied out of an editor session's log. They
 * exist as a test because the editor reaches the same CLI the participant does,
 * through verbs the terminal rarely uses, and the first version of the
 * classifier read only the first word after `sgt`.
 */
describe('sgt commands the editor emits', () => {
  it('files a grouped verb under the verb, not under its group', () => {
    expect(cat('sgt feature regroup split f-abc')).toBe('history_op')
    expect(cat('sgt feature regroup merge f-abc f-def')).toBe('history_op')
    expect(cat('sgt feature rename f-abc waitlist')).toBe('history_op')
  })

  it('does not count a preview as the operation it previews', () => {
    // Emitted on hover, several times a second, by the workbench rail.
    expect(cat('sgt advanced preview revert f-abc --json')).toBe('inspect')
    expect(cat('sgt advanced preview restore f-abc --json')).toBe('inspect')
  })

  it('does not count the editor reading as the participant operating', () => {
    // The extension reads with --json and mutates without it.
    expect(cat('sgt feature regroup split --json f-abc')).toBe('inspect')
    expect(cat('sgt feature rename --json f-abc waitlist')).toBe('inspect')
  })

  it('still reads plain reads as reads', () => {
    expect(cat('sgt log --json')).toBe('orient')
    expect(cat('sgt now --json')).toBe('orient')
    expect(cat('sgt plan status --json --full')).toBe('orient')
    expect(cat('sgt advanced fold --at 16 --json')).toBe('inspect')
    expect(cat('sgt advanced compose --json --full')).toBe('inspect')
  })

  it('still reads plain operations as operations', () => {
    expect(cat('sgt revert cart.py::total')).toBe('history_op')
    expect(cat('sgt save -m fixed the parser')).toBe('history_op')
    expect(cat('sgt restore cart.py::total')).toBe('history_op')
  })

  it('leaves git alone', () => {
    expect(cat('git commit -m x', 'git')).toBe('history_op')
    expect(cat('git log --oneline', 'git')).toBe('orient')
    expect(cat('git blame -- cart.py', 'git')).toBe('inspect')
  })
})
