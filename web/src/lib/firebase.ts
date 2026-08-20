import { initializeApp } from 'firebase/app'
import {
  getAuth,
  getRedirectResult,
  signInAnonymously,
  signInWithRedirect,
  GoogleAuthProvider,
  signInWithPopup,
  signOut,
  onAuthStateChanged,
  type Auth,
  type User,
} from 'firebase/auth'
import {
  connectFirestoreEmulator,
  initializeFirestore,
  memoryLocalCache,
  persistentLocalCache,
  persistentMultipleTabManager,
  type Firestore,
} from 'firebase/firestore'
import { connectAuthEmulator } from 'firebase/auth'

/**
 * `authDomain` is the domain the app is served from, not the default
 * `sem-git.firebaseapp.com` that the Firebase console hands out.
 *
 * The sign-in popup loads `<authDomain>/__/auth/handler` and talks back to the
 * opener. With the console default that popup is cross-origin, which browsers
 * now break in two independent ways: cross-origin-opener-policy severs the
 * opener reference so the popup cannot report back or close itself, and
 * third-party storage partitioning hides the auth state it wrote. The symptom
 * is a sign-in that visibly succeeds -- you pick the account -- and then does
 * nothing.
 *
 * Firebase Hosting serves `/__/auth/handler` on every domain of the project, so
 * naming the serving domain here makes the popup same-origin and both problems
 * disappear. If this app is ever served from a custom domain, this has to move
 * with it, and that domain has to be in Authentication -> Settings -> Authorized
 * domains.
 */
const AUTH_DOMAIN =
  typeof window !== 'undefined' && /(^|\.)(web\.app|firebaseapp\.com)$/.test(window.location.hostname)
    ? window.location.hostname
    : 'sem-git.web.app'

const firebaseConfig = {
  apiKey: 'AIzaSyDsFEnfbmk2Muj1amaYVvIsajEQM8OukNY',
  authDomain: AUTH_DOMAIN,
  projectId: 'sem-git',
  storageBucket: 'sem-git.firebasestorage.app',
  messagingSenderId: '1095260477565',
  appId: '1:1095260477565:web:4f7e086bfe63bcaa0cf914',
}

export const app = initializeApp(firebaseConfig)

// Offline persistence is the reason a dropped wifi connection mid-questionnaire
// costs nothing: writes queue locally and flush when the tab reconnects, and
// the UI's own optimistic state never diverges from what will land.
//
// It is a nice-to-have, not a requirement, and it is the one part of startup
// that depends on IndexedDB being available. A private window, a locked-down
// profile, or a browser that closes the database while the tab is backgrounded
// can all make it fail, and a study console that will not open because its
// cache would not initialise is worse than one with no cache.
function openFirestore(): Firestore {
  try {
    return initializeFirestore(app, {
      localCache: persistentLocalCache({ tabManager: persistentMultipleTabManager() }),
    })
  } catch (err) {
    console.warn('[study] falling back to an in-memory cache:', err)
    return initializeFirestore(app, { localCache: memoryLocalCache() })
  }
}

export const db = openFirestore()

export const auth = getAuth(app)

/**
 * Point the whole app at local emulators.
 *
 * This is how a facilitator rehearses a full session -- consent to debrief,
 * with real telemetry from a real bundle -- without putting a fake participant
 * into the real study. Every rehearsal that has to be cleaned up afterwards is
 * a chance to delete the wrong thing.
 *
 * Set VITE_USE_EMULATOR=1 when running the dev server. It can never be on in a
 * production build, because the flag is compiled in at build time.
 */
export const usingEmulators = import.meta.env.VITE_USE_EMULATOR === '1'

if (usingEmulators) {
  connectAuthEmulator(auth, 'http://127.0.0.1:9099', { disableWarnings: true })
  connectFirestoreEmulator(db, '127.0.0.1', 8080)
  console.info('[study] using local emulators; nothing here reaches the real study')
}

export function watchAuth(fn: (user: User | null) => void) {
  return onAuthStateChanged(auth, fn)
}

export async function ensureAnonymous(): Promise<User> {
  if (auth.currentUser) return auth.currentUser
  const cred = await signInAnonymously(auth)
  return cred.user
}

/**
 * Sign in as an experimenter without Google, against the auth emulator only.
 *
 * `usingEmulators` is a build-time constant, so this whole branch is dropped
 * from a production bundle. It exists so a facilitator can rehearse the console
 * as well as the participant flow -- the half of the study that is hardest to
 * practise is the half where you are also running a session.
 */
export async function signInForRehearsal(email: string): Promise<User> {
  if (!usingEmulators) throw new Error('rehearsal sign-in is only available against emulators')
  const { signInWithEmailAndPassword, createUserWithEmailAndPassword } = await import(
    'firebase/auth'
  )
  const password = 'rehearsal-only'
  try {
    const cred = await signInWithEmailAndPassword(auth, email, password)
    return cred.user
  } catch {
    const cred = await createUserWithEmailAndPassword(auth, email, password)
    return cred.user
  }
}

function googleProvider() {
  const provider = new GoogleAuthProvider()
  provider.setCustomParameters({ prompt: 'select_account' })
  return provider
}

/**
 * Sign the experimenter in, popup first and full-page redirect if that fails.
 *
 * The popup is nicer when it works, and it does not always work: a browser can
 * block it outright, and cross-origin-opener-policy can stop the popup closing
 * itself even after a successful sign-in, which surfaces as an error on a
 * sign-in that actually succeeded. The redirect path has neither problem, so it
 * is the fallback rather than an error message.
 *
 * `auth.currentUser` is checked first because a "failed" popup often did sign
 * the user in; reporting a failure in that case would send someone off to debug
 * a working console.
 */
export async function signInWithGoogle(): Promise<User | null> {
  try {
    const cred = await signInWithPopup(auth, googleProvider())
    return cred.user
  } catch (err) {
    if (auth.currentUser) return auth.currentUser
    const code = (err as { code?: string }).code ?? ''
    const recoverable =
      code === 'auth/popup-blocked' ||
      code === 'auth/popup-closed-by-user' ||
      code === 'auth/cancelled-popup-request' ||
      code === 'auth/internal-error' ||
      code === 'auth/web-storage-unsupported' ||
      /database|indexeddb|closing/i.test(String((err as Error).message ?? ''))
    if (!recoverable) throw err
    // Navigates away; the result is picked up by completeRedirectSignIn().
    await signInWithRedirect(auth, googleProvider())
    return null
  }
}

/**
 * Sign in by navigating away and coming back, with no popup involved.
 *
 * Offered as its own button rather than only as a fallback, because popup
 * sign-in has enough ways to fail quietly in a locked-down browser that the
 * person running the study should never be stuck without a second route in.
 */
export async function signInWithGoogleRedirect(): Promise<void> {
  await signInWithRedirect(auth, googleProvider())
}

/** Finish a redirect sign-in, if this load is the return leg of one. */
export async function completeRedirectSignIn(authInstance: Auth = auth): Promise<User | null> {
  try {
    const cred = await getRedirectResult(authInstance)
    return cred?.user ?? null
  } catch (err) {
    console.warn('[study] redirect sign-in did not complete:', err)
    return null
  }
}

export async function signOutNow() {
  await signOut(auth)
}
