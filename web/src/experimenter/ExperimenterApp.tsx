import { useEffect, useState } from 'react'
import type { User } from 'firebase/auth'
import {
  completeRedirectSignIn,
  signInForRehearsal,
  signInWithGoogle,
  signInWithGoogleRedirect,
  signOutNow,
  usingEmulators,
  watchAuth,
} from '../lib/firebase'
import { useLiveDoc } from '../lib/db'
import { Spinner, Tabs } from '../ui/bits'
import { Roster } from './Roster'
import { Monitor } from './Monitor'
import { ParticipantDetail } from './ParticipantDetail'
import { Results } from './Results'
import { Settings } from './Settings'

type TabId = 'monitor' | 'roster' | 'results' | 'settings'

/**
 * Must match `isOwner()` in firestore.rules. The owner is named in both places
 * rather than seeded into the database by hand, so there is no setup step that
 * can be forgotten and no window where the console is open to anyone.
 */
export const OWNER_EMAIL = 'ryanyen2@mit.edu'

export function ExperimenterApp() {
  const [user, setUser] = useState<User | null | undefined>(undefined)
  const [tab, setTab] = useState<TabId>('monitor')
  const [openPid, setOpenPid] = useState<string | null>(null)

  useEffect(() => watchAuth(setUser), [])

  // If this load is the return leg of a redirect sign-in, settle it before the
  // page decides the visitor is a stranger.
  useEffect(() => {
    void completeRedirectSignIn()
  }, [])

  const { data: adminDoc, loading: adminLoading } = useLiveDoc<{ role: string }>(
    user?.email ? ['admins', user.email] : null,
  )

  const isOwner = user?.email === OWNER_EMAIL

  if (user === undefined) return <Spinner label="Checking your sign-in" />
  if (!user || user.isAnonymous) return <SignIn />
  if (adminLoading && !isOwner) return <Spinner label="Checking access" />
  if (!adminDoc && !isOwner) return <NotAllowed email={user.email ?? '(no email)'} />

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            Study console <small>· sem-git</small>
          </div>
          <div className="spacer" />
          <span className="small muted">{user.email}</span>
          <button className="btn sm ghost" onClick={() => void signOutNow()}>
            Sign out
          </button>
        </div>
      </header>

      <div className="page wide">
        {openPid ? (
          <ParticipantDetail pid={openPid} onClose={() => setOpenPid(null)} adminEmail={user.email ?? ''} />
        ) : (
          <>
            <Tabs<TabId>
              value={tab}
              onChange={setTab}
              tabs={[
                { id: 'monitor', label: 'Live' },
                { id: 'roster', label: 'Participants' },
                { id: 'results', label: 'Results' },
                { id: 'settings', label: 'Setup' },
              ]}
            />
            {tab === 'monitor' && <Monitor onOpen={setOpenPid} />}
            {tab === 'roster' && <Roster onOpen={setOpenPid} />}
            {tab === 'results' && <Results />}
            {tab === 'settings' && <Settings />}
          </>
        )}
      </div>
    </div>
  )
}

function SignIn() {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  return (
    <div className="page">
      <div className="card">
        <h1>Study console</h1>
        <p className="lede">Sign in with the Google account on the study allowlist.</p>
        <button
          className="btn primary lg"
          disabled={busy}
          onClick={async () => {
            setError(null)
            setBusy(true)
            try {
              await signInWithGoogle()
            } catch (e) {
              setError((e as Error).message)
            } finally {
              setBusy(false)
            }
          }}
        >
          {busy ? 'Signing in' : 'Sign in with Google'}
        </button>
        <p className="small muted" style={{ marginTop: '0.85rem' }}>
          If the popup opens, you pick an account, and nothing happens,{' '}
          <button
            className="btn sm"
            onClick={() => {
              setError(null)
              void signInWithGoogleRedirect()
            }}
          >
            sign in without a popup
          </button>
        </p>
        {usingEmulators && (
          <p style={{ marginTop: '1rem' }}>
            <button
              className="btn"
              onClick={async () => {
                try {
                  await signInForRehearsal(OWNER_EMAIL)
                } catch (e) {
                  setError((e as Error).message)
                }
              }}
            >
              Sign in as {OWNER_EMAIL} (rehearsal only)
            </button>
          </p>
        )}
        {error && (
          <p className="small" style={{ color: 'var(--bad)', marginTop: '1rem' }}>
            {error}
          </p>
        )}
      </div>
    </div>
  )
}

function NotAllowed({ email }: { email: string }) {
  return (
    <div className="page">
      <div className="card">
        <h1>Not on the allowlist</h1>
        <p className="lede">
          <code>{email}</code> is signed in, but there is no matching entry.
        </p>
        <p className="small muted">
          The study owner is named in the security rules and does not need adding. Anyone else has
          to be added by an experimenter who is already in, from{' '}
          <strong>Setup → Who else can see this console</strong>. There is deliberately no way to
          let yourself in.
        </p>
        <button className="btn" onClick={() => void signOutNow()}>
          Sign out
        </button>
      </div>
    </div>
  )
}
