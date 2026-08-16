import { useEffect } from 'react'
import { useParticipant } from '../ParticipantApp'
import { patchParticipant, useLiveDoc } from '../../lib/db'
import type { PublicConfig } from '../../lib/types'
import { Markdown } from '../../ui/Markdown'
import { DEBRIEF_MD } from '../../study/content'

export function DoneStep() {
  const { pid, participant } = useParticipant()
  const { data: cfg } = useLiveDoc<PublicConfig>(['public', 'config'])

  useEffect(() => {
    if (participant.status !== 'completed') {
      void patchParticipant(pid, { status: 'completed', completedAt: Date.now() })
    }
  }, [pid, participant.status])

  return (
    <div className="stack loose">
      <div>
        <h1>Thank you</h1>
        <p className="lede">
          You are finished. Your participant code is <code>{participant.label}</code> — quote it if
          you ever want your data removed.
        </p>
      </div>

      <div className="card">
        <Markdown>{DEBRIEF_MD}</Markdown>
        {cfg?.supportEmail && (
          <p className="small muted">
            Questions or second thoughts: <a href={`mailto:${cfg.supportEmail}`}>{cfg.supportEmail}</a>
          </p>
        )}
      </div>

      <div className="card accent">
        <div className="strong">One last thing</div>
        <div className="small" style={{ color: 'var(--ink-2)' }}>
          Please do not describe the projects or the tasks to anyone who might take part later. Twelve
          people is a small study and one spoiled session is eight percent of it.
        </div>
      </div>
    </div>
  )
}
