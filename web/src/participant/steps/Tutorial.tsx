import { useParticipant } from '../ParticipantApp'
import { Markdown } from '../../ui/Markdown'
import { TUTORIAL_LEDE, tutorialFor } from '../../study/content'
import { blockFor, type Step } from '../../study/flow'
import { Callout } from '../../ui/bits'

export function TutorialStep({ step }: { step: Step }) {
  const { participant, goNext, goBack } = useParticipant()
  const block = blockFor(participant.blocks, step.half!)

  return (
    <div className="stack loose">
      <div>
        <div className="eyebrow">{block.label}</div>
        <h1>Practice</h1>
        <p className="lede">{TUTORIAL_LEDE}</p>
      </div>

      <Callout kind="accent" title="Nothing you do here can go wrong">
        You practise on the project the stages use, in the state it was in before the first stage.
        Running <code>./stage 1</code> at the start of the first stage undoes everything you do now.
      </Callout>

      <div className="card">
        <Markdown>{tutorialFor(block.condition, block.project)}</Markdown>
      </div>

      <div className="sticky-actions">
        <button className="btn primary lg" onClick={goNext}>
          I have tried these, continue
        </button>
        <button className="btn ghost" onClick={goBack}>
          Back
        </button>
      </div>
    </div>
  )
}
