import { useParticipant } from '../ParticipantApp'
import { Markdown } from '../../ui/Markdown'
import { WELCOME_MD } from '../../study/content'


export function WelcomeStep() {
  const { participant, goNext } = useParticipant()
  return (
    <div className="stack loose">
      <div>
        <div className="eyebrow">Participant {participant.label}</div>
        <h1>Welcome</h1>
        <p className="lede">
          Everything for today runs from this page. Work through it in order and it will tell you
          what to do next.
        </p>
      </div>

      <div className="card">
        <Markdown>{WELCOME_MD}</Markdown>
      </div>

      <div className="card accent">
        <div className="strong">Your answers save as you type</div>
        <div className="small" style={{ color: 'var(--ink-2)' }}>
          You can close this tab, lose your connection, or come back on another machine using the
          same link, and nothing you have entered will be gone.
        </div>
      </div>

      <div className="sticky-actions">
        <button className="btn primary lg" onClick={goNext}>
          Start
        </button>
      </div>
    </div>
  )
}
