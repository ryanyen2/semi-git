import { StrictMode, Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './styles.css'
import { Landing } from './Landing'
import { ParticipantApp } from './participant/ParticipantApp'
import { Spinner } from './ui/bits'
import { usingEmulators } from './lib/firebase'

/**
 * Impossible to mistake a rehearsal for a session. Without this, the two look
 * identical, and the first thing anyone would do with an identical-looking
 * rehearsal is wonder whether the real data is safe.
 */
function RehearsalBanner() {
  if (!usingEmulators) return null
  return (
    <div
      style={{
        background: '#a8620a',
        color: '#fff',
        padding: '0.3rem 1rem',
        fontSize: '0.8rem',
        fontWeight: 600,
        textAlign: 'center',
        letterSpacing: '0.02em',
      }}
    >
      Rehearsal mode — local emulators. Nothing here reaches the real study.
    </div>
  )
}

// The dashboard carries d3 and the whole analysis pipeline, which a participant
// never needs. Splitting it keeps their first load small on a hotel wifi, and
// keeps the scoring code out of the bundle they are served.
const ExperimenterApp = lazy(() =>
  import('./experimenter/ExperimenterApp').then((m) => ({ default: m.ExperimenterApp })),
)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <RehearsalBanner />
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/p/:code" element={<ParticipantApp />} />
        <Route
          path="/admin/*"
          element={
            <Suspense fallback={<Spinner label="Loading the console" />}>
              <ExperimenterApp />
            </Suspense>
          }
        />
        <Route path="*" element={<Landing />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
