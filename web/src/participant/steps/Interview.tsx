import { useParticipant } from '../ParticipantApp'
import { Markdown } from '../../ui/Markdown'
import { INTERVIEW_MD } from '../../study/content'
import { Callout } from '../../ui/bits'

// The own-repository walkthrough and the closing interview (protocol v2
// section 7). The interview itself is the facilitator's, run from the guide in
// the protocol; this page tells the participant what is about to happen and
// records nothing. Which path the interview took (their repository or the
// prepared one) is the facilitator's note on the roster, because the website
// cannot know whether a backfill that ran on the study machine finished.

export function InterviewStep() {
  const { goNext, goBack } = useParticipant()

  return (
    <div className="stack loose">
      <div>
        <div className="eyebrow">Finishing</div>
        <h1>Your own repository</h1>
      </div>

      <div className="card">
        <Markdown>{INTERVIEW_MD}</Markdown>
      </div>

      <Callout kind="soft" title="Nothing here is scored">
        This part is a conversation, not a task. Disagreeing with what the tool shows is exactly
        what we are here for.
      </Callout>

      <div className="sticky-actions">
        <button className="btn primary lg" onClick={goNext}>
          The interview is finished, continue
        </button>
        <button className="btn ghost" onClick={goBack}>
          Back
        </button>
      </div>
    </div>
  )
}
