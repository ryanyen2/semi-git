import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

/**
 * Two doors. Participants arrive with a full link and never see this page; it
 * exists for the one who pasted only the code, and for the experimenter.
 */
export function Landing() {
  const nav = useNavigate()
  const [code, setCode] = useState('')
  const clean = code.trim().replace(/^.*\/p\//, '').replace(/[^a-z0-9]/gi, '')

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            Working with project history <small>· user study</small>
          </div>
        </div>
      </header>
      <div className="page">
        <div className="stack loose">
          <div>
            <h1>Taking part today?</h1>
            <p className="lede">
              Your facilitator sent you a link. Open that and you will land in the right place. If
              you only have the code, paste it here.
            </p>
          </div>

          <div className="card">
            <form
              className="row"
              onSubmit={(e) => {
                e.preventDefault()
                if (clean) nav(`/p/${clean}`)
              }}
            >
              <input
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="Your code, or the whole link"
                aria-label="Participant code"
                style={{ flex: 1, minWidth: '16rem' }}
              />
              <button className="btn primary" type="submit" disabled={!clean}>
                Continue
              </button>
            </form>
          </div>

          <div className="row">
            <span className="small muted">Running the study?</span>
            <button className="btn sm" onClick={() => nav('/admin')}>
              Experimenter sign in
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
