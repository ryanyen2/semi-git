import { useState } from 'react'
import { useParticipant } from '../ParticipantApp'
import { useAutosaveForm } from '../../lib/db'
import { instrumentById } from '../../study/instruments'
import { Form, missingItems } from '../../ui/Form'
import { SaveChip } from '../../ui/bits'
import { blockFor, type Step } from '../../study/flow'

/**
 * Generic instrument page.
 *
 * It used to carry two extra behaviours for the quiz and the summary: a visible
 * three-minute clock, and a lock after submission so a participant could not
 * reopen the project and improve a memory answer. Both instruments are gone,
 * and with them the only steps that needed either, so the page is now what its
 * name says it is.
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

  if (!instrument) return <div className="card">Unknown questionnaire.</div>

  const missing = missingItems(instrument, form.values)

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

      <div className="card">
        <Form
          instrument={instrument}
          values={form.values}
          setValue={form.setValue}
          missing={tried ? missing : undefined}
        />
      </div>

      <div className="sticky-actions">
        <button className="btn primary lg" onClick={onSubmit} disabled={busy || !form.ready}>
          {busy ? 'Saving' : 'Save and continue'}
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
