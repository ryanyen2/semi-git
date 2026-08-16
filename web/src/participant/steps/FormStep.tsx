import { useEffect, useState } from 'react'
import { useParticipant } from '../ParticipantApp'
import { useAutosaveForm } from '../../lib/db'
import { instrumentById } from '../../study/instruments'
import { Form, missingItems } from '../../ui/Form'
import { Callout, SaveChip, fmtClock } from '../../ui/bits'
import { blockFor, type Step } from '../../study/flow'

/**
 * Generic instrument page. Quiz and summary get two extra behaviours: a visible
 * three-minute clock, and a lock after submission.
 *
 * The lock matters. Both instruments ask the participant to answer with the
 * project closed, and an unlocked page is an invitation to reopen the project
 * and improve the answer, which would quietly turn a memory measure into a
 * lookup measure.
 */
export function FormStep({ step }: { step: Step }) {
  const { pid, participant, goNext, goBack } = useParticipant()
  const instrument = instrumentById(step.instrumentId ?? '')
  const block = step.half ? blockFor(participant.blocks, step.half) : null
  const form = useAutosaveForm(
    pid,
    instrument?.id ?? 'unknown',
    instrument?.version ?? 'unknown',
    step.half,
    block?.condition ?? null,
  )
  const [tried, setTried] = useState(false)
  const [busy, setBusy] = useState(false)
  const [startedAt] = useState(() => Date.now())
  const [now, setNow] = useState(Date.now())

  const timed = step.kind === 'summary'
  const locked = (step.kind === 'quiz' || step.kind === 'summary') && form.submittedAt != null

  useEffect(() => {
    if (!timed || locked) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [timed, locked])

  if (!instrument) return <div className="card">Unknown questionnaire.</div>

  const missing = missingItems(instrument, form.values)
  const remaining = 3 * 60_000 - (now - startedAt)

  async function onSubmit() {
    setTried(true)
    if (missing.size > 0) return
    setBusy(true)
    try {
      await form.submit()
      await goNext()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack loose">
      <div>
        {block && <div className="eyebrow">{block.label}</div>}
        <h1>{instrument.title}</h1>
        {instrument.intro && <p className="lede">{instrument.intro}</p>}
      </div>

      {step.kind === 'quiz' && !locked && (
        <Callout kind="warn" title="Project closed, please">
          Answer from what you remember. Getting these wrong is expected and it is useful data, so
          please do not go and look them up.
        </Callout>
      )}

      {timed && !locked && (
        <div className="card soft row" style={{ justifyContent: 'space-between' }}>
          <span className="small muted">Aim for about three minutes. Nothing stops when it runs out.</span>
          <span className={`timer${remaining < 30_000 ? ' warn' : ''}`} style={{ fontSize: '1.15rem' }}>
            {fmtClock(remaining)}
          </span>
        </div>
      )}

      {locked && (
        <Callout kind="soft" title="Answered">
          You have already submitted this one, so it is read-only now. Carry on to the next step.
        </Callout>
      )}

      <div className="card">
        <Form
          instrument={instrument}
          values={form.values}
          setValue={form.setValue}
          disabled={locked}
          missing={tried ? missing : undefined}
        />
      </div>

      <div className="sticky-actions">
        <button className="btn primary lg" onClick={locked ? goNext : onSubmit} disabled={busy || !form.ready}>
          {locked ? 'Continue' : busy ? 'Saving' : 'Save and continue'}
        </button>
        <button className="btn ghost" onClick={goBack} disabled={busy}>
          Back
        </button>
        <SaveChip state={form.saveState} />
        {tried && missing.size > 0 && (
          <span className="small" style={{ color: 'var(--bad)' }}>
            {missing.size} still to answer.
          </span>
        )}
      </div>
    </div>
  )
}
