import { createContext, useContext, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ensureAnonymous } from '../lib/firebase'
import { claimParticipant, isPilot, patchParticipant, setStep, useLiveDoc } from '../lib/db'
import type { Participant } from '../lib/types'
import { STEPS, nextStepId, prevStepId, stepById, stepIndex } from '../study/flow'
import { Rail, Spinner } from '../ui/bits'
import { WelcomeStep } from './steps/Welcome'
import { ConsentStep } from './steps/Consent'
import { FormStep } from './steps/FormStep'
import { SetupStep } from './steps/Setup'
import { TutorialStep } from './steps/Tutorial'
import { TasksStep } from './steps/Tasks'
import { HandoverStep } from './steps/Handover'
import { DoneStep } from './steps/Done'

interface Ctx {
  pid: string
  participant: Participant
  goNext: () => Promise<void>
  goBack: () => Promise<void>
  goTo: (stepId: string) => Promise<void>
}

const ParticipantContext = createContext<Ctx | null>(null)

export function useParticipant(): Ctx {
  const c = useContext(ParticipantContext)
  if (!c) throw new Error('useParticipant outside provider')
  return c
}

export function ParticipantApp() {
  const { code } = useParams<{ code: string }>()
  const [authReady, setAuthReady] = useState(false)
  const [claimError, setClaimError] = useState<string | null>(null)
  const { data: participant, loading, error } = useLiveDoc<Participant>(
    code && authReady ? ['participants', code] : null,
  )

  useEffect(() => {
    let dead = false
    ;(async () => {
      try {
        const user = await ensureAnonymous()
        if (!code) return
        await claimParticipant(code, user.uid)
        if (!dead) setAuthReady(true)
      } catch (e) {
        if (!dead) {
          setClaimError((e as Error).message)
          setAuthReady(true)
        }
      }
    })()
    return () => {
      dead = true
    }
  }, [code])

  // Presence, so the dashboard can tell "away from keyboard" from "closed the
  // laptop and went home".
  useEffect(() => {
    if (!code || !participant) return
    const beat = () => void patchParticipant(code, { lastSeenAt: Date.now() }).catch(() => {})
    beat()
    const t = window.setInterval(beat, 30_000)
    return () => window.clearInterval(t)
  }, [code, participant?.code])

  if (!code) return <BadLink message="That link is missing its code." />
  if (!authReady || loading) return <Spinner label="Opening your session" />
  if (claimError) return <BadLink message={claimError} />
  if (error) return <BadLink message={error.message} />
  if (!participant) return <BadLink message="We could not find a session for that link." />

  const step = stepById(participant.currentStep) ?? STEPS[0]

  const ctx: Ctx = {
    pid: code,
    participant: { ...participant, code },
    goNext: async () => {
      await setStep(code, nextStepId(step.id))
      window.scrollTo({ top: 0, behavior: 'smooth' })
    },
    goBack: async () => {
      await setStep(code, prevStepId(step.id))
      window.scrollTo({ top: 0, behavior: 'smooth' })
    },
    goTo: async (id: string) => {
      await setStep(code, id)
      window.scrollTo({ top: 0, behavior: 'smooth' })
    },
  }

  return (
    <ParticipantContext.Provider value={ctx}>
      <div className="shell">
        <header className="topbar">
          <div className="topbar-inner">
            <div className="brand">
              Working with project history <small>· study session</small>
            </div>
            <div className="spacer" />
            <span className="badge outline">{participant.label}</span>
            {/* Visible to whoever holds the link, on every step. A pilot link
                handed to a real participant by mistake is otherwise completely
                indistinguishable from a real one -- same flow, same bundle --
                and would be found only in the analysis, afterwards. */}
            {isPilot(participant) && (
              <span className="badge warn" title="Rehearsal session — not part of the study">
                rehearsal
              </span>
            )}
            <span className="small muted nowrap">
              Step {stepIndex(step.id) + 1} of {STEPS.length}
            </span>
          </div>
        </header>
        <div className="layout">
          <Rail currentStep={step.id} />
          <main>
            <StepBody stepId={step.id} />
          </main>
        </div>
      </div>
    </ParticipantContext.Provider>
  )
}

function StepBody({ stepId }: { stepId: string }) {
  const step = stepById(stepId)
  if (!step) return null
  switch (step.kind) {
    case 'welcome':
      return <WelcomeStep />
    case 'consent':
      return <ConsentStep />
    case 'setup':
      return <SetupStep step={step} />
    case 'tutorial':
      return <TutorialStep step={step} />
    case 'tasks':
      return <TasksStep step={step} />
    case 'form':
    case 'quiz':
    case 'summary':
    case 'preference':
      return <FormStep step={step} />
    case 'handover':
      return <HandoverStep />
    case 'done':
      return <DoneStep />
  }
}

function BadLink({ message }: { message: string }) {
  return (
    <div className="page">
      <div className="card">
        <h1>We could not open that session</h1>
        <p className="lede">{message}</p>
        <p className="muted small">
          Check that you used the whole link, including everything after the last slash. If it still
          does not work, tell your facilitator. Nothing is lost.
        </p>
      </div>
    </div>
  )
}
