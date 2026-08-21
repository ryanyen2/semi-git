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

      <Callout kind="accent" title="Practice on the warm-up copy, not the real project">
        In the study shell, run <code>study-practice</code>. It drops you into a throwaway copy that
        is not one of the two study projects, so anything you do to it is free.
      </Callout>

      <div className="card">
        <Markdown>{tutorialFor(block.condition)}</Markdown>
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
