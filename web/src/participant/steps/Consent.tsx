import { useState } from 'react'
import { useParticipant } from '../ParticipantApp'
import { useAutosaveForm, patchParticipant, useLiveDoc } from '../../lib/db'
import { CONSENT } from '../../study/instruments'
import { Form, missingItems } from '../../ui/Form'
import { SaveChip } from '../../ui/bits'
import { Markdown } from '../../ui/Markdown'
import type { PublicConfig } from '../../lib/types'

const FALLBACK_INFO = `
## What we are asking you to do

You will work on two small programs you have never seen, using two different setups for reading and changing their history, with an AI coding assistant available in both. We are comparing the two setups.

## What we record

Your screen and voice for the session. Your answers on this site. The commands you run and the messages you send to the assistant, captured by a small logger inside the study folder. Nothing outside that folder is read.

## What we do not record

Anything from before or after the session. Any file outside the study folder. Any account of yours. The assistant runs on a key we issue and revoke.

## Storing and sharing

Data is stored under a participant code. Your name appears only on this consent record, kept apart from everything else. Results are reported in aggregate in an academic publication. De-identified data may be shared with other researchers.

## Your choices

You can stop at any time, for any reason or none, and still be paid in full. You can ask us to stop recording at any point. You can ask us to delete your data afterwards by emailing your participant code.
`.trim()

export function ConsentStep() {
  const { pid, goNext } = useParticipant()
  const { data: cfg } = useLiveDoc<PublicConfig>(['public', 'config'])
  const form = useAutosaveForm(pid, CONSENT.id, CONSENT.version, null, null)
  const [tried, setTried] = useState(false)
  const [busy, setBusy] = useState(false)

  const missing = missingItems(CONSENT, form.values)

  async function onSubmit() {
    setTried(true)
    if (missing.size > 0) return
    setBusy(true)
    try {
      await form.submit()
      await patchParticipant(pid, { consentAt: Date.now(), status: 'consented' })
      await goNext()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack loose">
      <div>
        <h1>Consent</h1>
        <p className="lede">
          Read this, ask anything you want, then tick what you agree to. Nothing here is a formality
          and you can change your mind later.
        </p>
      </div>

      <div className="card">
        <Markdown>{cfg?.consentBodyMarkdown?.trim() || FALLBACK_INFO}</Markdown>
        {cfg?.compensation && (
          <p className="small muted">
            <strong>Compensation.</strong> {cfg.compensation}
          </p>
        )}
        {cfg?.irbProtocol && (
          <p className="tiny faint">Protocol {cfg.irbProtocol}. Questions: {cfg.supportEmail}</p>
        )}
      </div>

      <div className="card">
        <h2>{CONSENT.title}</h2>
        <p className="small muted">{CONSENT.intro}</p>
        <Form
          instrument={CONSENT}
          values={form.values}
          setValue={form.setValue}
          missing={tried ? missing : undefined}
        />
      </div>

      <div className="sticky-actions">
        <button className="btn primary lg" onClick={onSubmit} disabled={busy || !form.ready}>
          {busy ? 'Saving' : 'I agree, continue'}
        </button>
        <SaveChip state={form.saveState} />
        {tried && missing.size > 0 && (
          <span className="small" style={{ color: 'var(--bad)' }}>
            Please tick the required lines and type your name.
          </span>
        )}
      </div>
    </div>
  )
}
