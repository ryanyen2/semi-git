import { useParticipant } from '../ParticipantApp'
import { Markdown } from '../../ui/Markdown'
import { BRIEF_NO_MEMORISE, BRIEF_UNTIMED, PROJECT_BRIEF } from '../../study/content'
import { blockFor, type Step } from '../../study/flow'
import { SCENARIO } from '../../study/tasks'
import { Callout } from '../../ui/bits'

// The project, before any clock exists.
//
// Pilots met the codebase for the first time on the first request, with the
// countdown already running, and spent the first third of their budget working
// out what the app was for. That is not what the study is measuring, and it is
// not evenly distributed either: whichever project a participant sees second is
// cheaper to orient in, because the two are the same shape under different
// nouns. Nothing on this page starts, opens or patches a request.

export function BriefStep({ step }: { step: Step }) {
  const { participant, goNext, goBack } = useParticipant()
  const block = blockFor(participant.blocks, step.half!)
  const scenario = SCENARIO[block.project]

  return (
    <div className="stack loose">
      <div>
        <div className="eyebrow">
          {block.label} · {scenario.app}
        </div>
        <h1>The project</h1>
        <p className="lede">
          What {scenario.app} does, who uses it, and what it refuses to do. {BRIEF_UNTIMED}
        </p>
      </div>

      <div className="card">
        <Markdown>{PROJECT_BRIEF[block.project]}</Markdown>
      </div>

      <Callout kind="soft" title="You do not have to memorise any of this">
        {BRIEF_NO_MEMORISE}
      </Callout>

      <div className="sticky-actions">
        <button className="btn primary lg" onClick={goNext}>
          I have read this, continue
        </button>
        <button className="btn ghost" onClick={goBack}>
          Back
        </button>
      </div>
    </div>
  )
}
