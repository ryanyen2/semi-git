import {
  collection,
  deleteDoc,
  doc,
  getDoc,
  getDocs,
  onSnapshot,
  orderBy,
  query,
  serverTimestamp,
  setDoc,
  updateDoc,
  writeBatch,
  type DocumentData,
  type QueryConstraint,
} from 'firebase/firestore'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { db } from './firebase'
import type {
  BlockAssignment,
  Condition,
  EventDoc,
  Half,
  Participant,
  ParticipantStatus,
  RequestDoc,
  RequestId,
  ResponseDoc,
  StudyId,
} from './types'
import { blocksForGroup, groupForOrdinal } from '../study/flow'

export const participantRef = (pid: string) => doc(db, 'participants', pid)
export const responsesCol = (pid: string) => collection(db, 'participants', pid, 'responses')
export const requestsCol = (pid: string) => collection(db, 'participants', pid, 'requests')
export const eventsCol = (pid: string) => collection(db, 'participants', pid, 'events')
export const devicesCol = (pid: string) => collection(db, 'participants', pid, 'devices')
export const scoringCol = (pid: string) => collection(db, 'participants', pid, 'scoring')
export const notesCol = (pid: string) => collection(db, 'participants', pid, 'notes')

// ---------------------------------------------------------------------------
// Live subscriptions
// ---------------------------------------------------------------------------

export type Loadable<T> = { data: T | null; loading: boolean; error: Error | null }

export function useLiveDoc<T>(path: string[] | null): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ data: null, loading: true, error: null })
  const key = path ? path.join('/') : ''
  useEffect(() => {
    if (!path) {
      setState({ data: null, loading: false, error: null })
      return
    }
    const ref = doc(db, path[0], ...path.slice(1))
    return onSnapshot(
      ref,
      (snap) =>
        setState({
          data: snap.exists() ? ({ id: snap.id, ...snap.data() } as T) : null,
          loading: false,
          error: null,
        }),
      (err) => setState({ data: null, loading: false, error: err as Error }),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return state
}

export function useLiveCollection<T>(
  path: string[] | null,
  ...constraints: QueryConstraint[]
): Loadable<T[]> {
  const [state, setState] = useState<Loadable<T[]>>({ data: null, loading: true, error: null })
  const key = path ? path.join('/') : ''
  const cKey = constraints.length
  useEffect(() => {
    if (!path) {
      setState({ data: null, loading: false, error: null })
      return
    }
    const col = collection(db, path[0], ...path.slice(1))
    return onSnapshot(
      constraints.length ? query(col, ...constraints) : col,
      (snap) =>
        setState({
          data: snap.docs.map((d) => ({ id: d.id, ...d.data() }) as T),
          loading: false,
          error: null,
        }),
      (err) => setState({ data: null, loading: false, error: err as Error }),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, cKey])
  return state
}

// ---------------------------------------------------------------------------
// Participants
// ---------------------------------------------------------------------------

const CODE_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'

/**
 * 24 characters from a 31-letter alphabet, no lookalikes. That is about 118
 * bits: the link is the only credential a participant has, so it has to be
 * unguessable, and it also gets read aloud over a video call often enough that
 * dropping `l`, `1`, `o` and `0` is worth the two bits.
 */
export function newAccessCode(): string {
  const bytes = new Uint8Array(24)
  crypto.getRandomValues(bytes)
  return [...bytes].map((b) => CODE_ALPHABET[b % CODE_ALPHABET.length]).join('')
}

/**
 * True for a rehearsal record. Read it, never re-derive it from the label: the
 * whole point is that one field decides, and every surface asks the same one.
 */
export function isPilot(p: { studyId?: string }): boolean {
  return p.studyId === 'pilot'
}

/** Pilot ordinals live above this, so they sort last and can never take P13. */
export const PILOT_ORDINAL_BASE = 1000

/**
 * Who the `seq`-th record of `studyId` is: its sort key, its printed name, and
 * its counterbalancing group.
 *
 * Pure, and separate from `createCohort`, because this is where the guarantee
 * that makes rehearsing safe actually lives -- each study numbers itself, so
 * however many pilots have been run, `Create 12` still yields exactly P01..P12
 * in groups 1,2,3,4,1,2,... A pilot gets a real group for the same reason it
 * gets a real bundle: a rehearsal of a condition order has to have one.
 */
export function participantIdentity(studyId: StudyId, seq: number) {
  const pilot = studyId === 'pilot'
  return {
    ordinal: pilot ? PILOT_ORDINAL_BASE + seq : seq,
    label: `${pilot ? 'X' : 'P'}${String(seq).padStart(2, '0')}`,
    group: groupForOrdinal(seq),
  }
}

/**
 * Create `count` participant records, numbered from `startingSeq` within their
 * own study. See `participantIdentity` for what "their own study" buys.
 */
export async function createCohort(
  studyId: StudyId,
  count: number,
  startingSeq = 1,
): Promise<Participant[]> {
  const batch = writeBatch(db)
  const now = Date.now()
  const made: Participant[] = []
  for (let i = 0; i < count; i++) {
    const { ordinal, label, group } = participantIdentity(studyId, startingSeq + i)
    const code = newAccessCode()
    const p: Participant = {
      code,
      studyId,
      ordinal,
      label,
      group,
      blocks: blocksForGroup(group),
      email: null,
      status: 'created',
      currentStep: 'welcome',
      stepState: {},
      claimedUid: null,
      claimedAt: null,
      startedAt: null,
      consentAt: null,
      completedAt: null,
      lastSeenAt: null,
      createdAt: now,
      updatedAt: now,
    }
    batch.set(doc(db, 'participants', code), p)
    made.push(p)
  }
  await batch.commit()
  return made
}

export async function getParticipant(pid: string): Promise<Participant | null> {
  const snap = await getDoc(participantRef(pid))
  return snap.exists() ? ({ ...(snap.data() as Participant), code: snap.id }) : null
}

export async function claimParticipant(pid: string, uid: string): Promise<void> {
  const snap = await getDoc(participantRef(pid))
  if (!snap.exists()) throw new Error('That link does not match a participant record.')
  const p = snap.data() as Participant
  if (p.claimedUid && p.claimedUid !== uid) {
    // Not fatal: the same person on a second browser is the common cause. The
    // facilitator can see both in the dashboard and decide.
    throw new Error(
      'This link has already been opened in another browser. Tell your facilitator before you go on, so we do not end up with two half-sessions.',
    )
  }
  const patch: Partial<Participant> = {
    updatedAt: Date.now(),
    lastSeenAt: Date.now(),
  }
  if (!p.claimedUid) {
    patch.claimedUid = uid
    patch.claimedAt = Date.now()
    patch.status = p.status === 'created' ? 'claimed' : p.status
    patch.startedAt = p.startedAt ?? Date.now()
  }
  await updateDoc(participantRef(pid), patch as DocumentData)
}

export async function patchParticipant(pid: string, patch: Partial<Participant>): Promise<void> {
  await updateDoc(participantRef(pid), { ...patch, updatedAt: Date.now() } as DocumentData)
}

/**
 * Every subcollection hanging off a participant. Named in one place because a
 * delete that misses one leaves orphans nothing can reach: the record is the
 * only path to them, so once it is gone they are invisible *and* undeletable
 * from this console forever.
 */
export const PARTICIPANT_SUBCOLLECTIONS = [
  'responses', 'requests', 'events', 'devices', 'secrets', 'scoring', 'notes',
] as const

/** What a participant is carrying, so a destructive action can state it first. */
export async function participantFootprint(pid: string): Promise<Record<string, number>> {
  const counts = await Promise.all(
    PARTICIPANT_SUBCOLLECTIONS.map(async (name) => {
      // A missing subcollection reads as empty, not as an error.
      const snap = await getDocs(collection(db, 'participants', pid, name)).catch(() => null)
      return [name, snap ? snap.size : 0] as const
    }),
  )
  return Object.fromEntries(counts.filter(([, n]) => n > 0))
}

async function purgeSubcollections(pid: string): Promise<number> {
  let removed = 0
  for (const name of PARTICIPANT_SUBCOLLECTIONS) {
    const snap = await getDocs(collection(db, 'participants', pid, name)).catch(() => null)
    if (!snap || snap.empty) continue
    // Batched in chunks: Firestore caps a batch at 500 writes, and a session
    // routinely produces more events than that -- pilot 03 recorded 339 from
    // one participant, and a real one on a bad day will exceed the cap.
    const docs = snap.docs
    for (let i = 0; i < docs.length; i += 400) {
      const batch = writeBatch(db)
      for (const d of docs.slice(i, i + 400)) batch.delete(d.ref)
      await batch.commit()
      removed += Math.min(400, docs.length - i)
    }
  }
  return removed
}

/**
 * Wipe a participant's data but keep the person: same code, same link, same
 * counterbalancing assignment, back at step one.
 *
 * This is the action that was missing. Deleting was the only recovery offered,
 * and it is the wrong one for every case that actually happens -- a pilot you
 * want to run again, a session abandoned halfway, a participant who has to
 * reschedule. Those all want the assignment kept (it is what makes the cohort
 * balanced) and the data gone.
 */
export async function resetParticipant(pid: string): Promise<number> {
  const removed = await purgeSubcollections(pid)
  await updateDoc(participantRef(pid), {
    status: 'created',
    currentStep: 'welcome',
    stepState: {},
    claimedUid: null,
    claimedAt: null,
    startedAt: null,
    consentAt: null,
    completedAt: null,
    lastSeenAt: null,
    updatedAt: Date.now(),
  } as DocumentData)
  return removed
}

/**
 * Remove a participant and everything underneath them. Subcollections go
 * first: if the record went first and the purge then failed, the leftovers
 * would be unreachable.
 */
export async function deleteParticipantDeep(pid: string): Promise<number> {
  const removed = await purgeSubcollections(pid)
  await deleteDoc(participantRef(pid))
  return removed
}

export async function setStep(pid: string, stepId: string): Promise<void> {
  await patchParticipant(pid, { currentStep: stepId, lastSeenAt: Date.now() })
}

export async function setStatus(pid: string, status: ParticipantStatus): Promise<void> {
  await patchParticipant(pid, { status })
}

// ---------------------------------------------------------------------------
// Responses, with a local mirror
// ---------------------------------------------------------------------------

type Values = ResponseDoc['values']

/**
 * A crash-safe local mirror of whatever is currently typed.
 *
 * Firestore's own offline cache already survives a refresh or a dropped
 * network, so this is not about those. It is about the two cases the cache
 * cannot help with: the browser dying between a keystroke and the debounce
 * firing, and a `pagehide` that never arrives (a force-quit, an OS kill, a
 * crashed tab). Every keystroke lands here synchronously, so the worst case is
 * one keystroke rather than one answer.
 */
export function draftKey(...parts: string[]): string {
  return ['sgt-study', ...parts].join('/')
}

export function readDraft<T>(key: string): { value: T; at: number } | null {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function writeDraft<T>(key: string, value: T) {
  try {
    localStorage.setItem(key, JSON.stringify({ value, at: Date.now() }))
  } catch {
    /* a full or disabled localStorage must not break the session */
  }
}

export function clearDraft(key: string) {
  try {
    localStorage.removeItem(key)
  } catch {
    /* nothing to do; a stale draft is discarded on next load by its timestamp */
  }
}

/**
 * Run `flush` when the page is going away.
 *
 * Both events, deliberately. `pagehide` is the reliable one on desktop; on
 * mobile and on tab-switch it is often never delivered at all, and
 * `visibilitychange` is what fires instead. Registering only one loses work on
 * whichever platform it is not.
 */
export function useFlushOnHide(flush: () => void) {
  const latest = useRef(flush)
  latest.current = flush
  useEffect(() => {
    const run = () => latest.current()
    window.addEventListener('pagehide', run)
    document.addEventListener('visibilitychange', run)
    return () => {
      window.removeEventListener('pagehide', run)
      document.removeEventListener('visibilitychange', run)
    }
  }, [])
}

const mirrorKey = (pid: string, docId: string) => draftKey(pid, docId)

function readMirror(pid: string, docId: string): { values: Values; at: number } | null {
  const hit = readDraft<Values>(mirrorKey(pid, docId))
  return hit ? { values: hit.value, at: hit.at } : null
}

function writeMirror(pid: string, docId: string, values: Values) {
  writeDraft(mirrorKey(pid, docId), values)
}

export type SaveState = 'idle' | 'saving' | 'saved' | 'error'

/**
 * A form bound to one response document.
 *
 * Three layers, deliberately. Every keystroke lands in React state so the field
 * is responsive; every keystroke also lands in localStorage so a browser crash
 * before the debounce fires loses nothing; and a debounced write goes to
 * Firestore, which has offline persistence of its own, so a dropped network
 * queues rather than fails. Losing a questionnaire is the one failure this
 * study cannot recover from, because you cannot ask someone to feel the same
 * way twice.
 */
export function useAutosaveForm(
  pid: string | null,
  instrumentId: string,
  version: string,
  half: Half | null,
  condition: Condition | null,
) {
  const docId = half ? `${instrumentId}-h${half}` : instrumentId
  const [values, setValues] = useState<Values>({})
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [ready, setReady] = useState(false)
  const [submittedAt, setSubmittedAt] = useState<number | null>(null)
  const startedAt = useRef<number>(Date.now())
  const timer = useRef<number | null>(null)
  const pending = useRef<Values | null>(null)

  // Load once: remote first, then the local mirror if it is newer.
  useEffect(() => {
    let cancelled = false
    if (!pid) return
    ;(async () => {
      const snap = await getDoc(doc(db, 'participants', pid, 'responses', docId))
      const remote = snap.exists() ? (snap.data() as ResponseDoc) : null
      const local = readMirror(pid, docId)
      const remoteAt = remote?.submittedAt ?? 0
      const useLocal = local && local.at > remoteAt
      if (cancelled) return
      setValues(useLocal ? local!.values : (remote?.values ?? {}))
      setSubmittedAt(remote?.submittedAt ?? null)
      startedAt.current = remote?.startedAt ?? Date.now()
      setReady(true)
      if (useLocal) void flush(local!.values)
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, docId])

  const flush = useCallback(
    async (v: Values, submit = false) => {
      if (!pid) return
      setSaveState('saving')
      try {
        const payload: Partial<ResponseDoc> & DocumentData = {
          instrumentId,
          version,
          half,
          condition,
          values: v,
          startedAt: startedAt.current,
          dwellMs: Date.now() - startedAt.current,
        }
        if (submit) payload.submittedAt = Date.now()
        await setDoc(doc(db, 'participants', pid, 'responses', docId), payload, { merge: true })
        if (submit) setSubmittedAt(Date.now())
        setSaveState('saved')
      } catch (e) {
        console.error('autosave failed', e)
        setSaveState('error')
      }
    },
    [pid, docId, instrumentId, version, half, condition],
  )

  const setValue = useCallback(
    (itemId: string, value: Values[string]) => {
      setValues((prev) => {
        const next = { ...prev, [itemId]: value }
        if (pid) writeMirror(pid, docId, next)
        pending.current = next
        if (timer.current) window.clearTimeout(timer.current)
        timer.current = window.setTimeout(() => {
          if (pending.current) void flush(pending.current)
        }, 600)
        return next
      })
    },
    [pid, docId, flush],
  )

  // A closing tab is the most common way a debounced write is lost.
  useEffect(() => {
    const onHide = () => {
      if (pending.current) void flush(pending.current)
    }
    window.addEventListener('pagehide', onHide)
    document.addEventListener('visibilitychange', onHide)
    return () => {
      window.removeEventListener('pagehide', onHide)
      document.removeEventListener('visibilitychange', onHide)
    }
  }, [flush])

  const submit = useCallback(async () => {
    if (timer.current) window.clearTimeout(timer.current)
    await flush(pending.current ?? values, true)
  }, [flush, values])

  return { values, setValue, submit, saveState, ready, submittedAt }
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

export const requestDocId = (requestId: RequestId, half: Half) => `${requestId}-h${half}`

export async function openRequest(
  pid: string,
  requestId: RequestId,
  block: BlockAssignment,
  capMs: number,
): Promise<void> {
  const id = requestDocId(requestId, block.half)
  const ref = doc(db, 'participants', pid, 'requests', id)
  const snap = await getDoc(ref)
  if (snap.exists() && (snap.data() as RequestDoc).openedAt) return
  const seed: RequestDoc = {
    requestId,
    half: block.half,
    condition: block.condition,
    project: block.project,
    openedAt: Date.now(),
    submittedAt: null,
    elapsedMs: 0,
    activeMs: 0,
    pauses: [],
    capMs,
    hitCap: false,
    choices: {},
    confidence: null,
    selfReport: null,
    notes: '',
  }
  await setDoc(ref, seed, { merge: true })
}

export async function patchRequest(
  pid: string,
  requestId: RequestId,
  half: Half,
  patch: Partial<RequestDoc>,
): Promise<void> {
  await setDoc(doc(db, 'participants', pid, 'requests', requestDocId(requestId, half)), defined(patch), {
    merge: true,
  })
}

/**
 * Drop keys whose value is `undefined`.
 *
 * Firestore rejects the whole write when any field is `undefined` -- not the
 * field, the write -- and a rejected write on this path is a participant who
 * cannot submit a stage. That happened on the first live session: the answer
 * state seeds `confidenceScale: doc?.confidenceScale`, which is `undefined`
 * until the confidence slider is touched, and stages 1 and 4 never show one. So
 * every autosave and every Submit on those stages threw
 * `Unsupported field value: undefined (found in field confidenceScale)` and the
 * button did nothing, with the reason only in the browser console.
 *
 * An optional field on a document is normal and will happen again; the fix
 * belongs at the boundary rather than at each call site. TypeScript cannot catch
 * this -- `{confidenceScale: undefined}` type-checks against
 * `{confidenceScale?: 7}` -- so nothing but the write itself ever objected.
 */
function defined<T extends object>(patch: T): T {
  return Object.fromEntries(Object.entries(patch).filter(([, v]) => v !== undefined)) as T
}

/**
 * A boundary marker written into the same event stream the bundle writes to,
 * so telemetry can be sliced by request without the participant's machine
 * having to know what a request is.
 */
export async function markRequestBoundary(
  pid: string,
  requestId: RequestId,
  block: BlockAssignment,
  edge: 'open' | 'close',
): Promise<void> {
  const id = `marker-${block.half}-${requestId}-${edge}`
  await setDoc(
    doc(db, 'participants', pid, 'events', id),
    {
      id,
      kind: 'marker',
      ts: Date.now(),
      half: block.half,
      condition: block.condition,
      requestId,
      name: edge,
      text: null,
      deviceId: 'web',
    } satisfies EventDoc,
    { merge: true },
  )
}

// ---------------------------------------------------------------------------
// Bulk reads for the dashboard
// ---------------------------------------------------------------------------

export async function fetchAll<T>(path: string[], ...constraints: QueryConstraint[]): Promise<T[]> {
  const col = collection(db, path[0], ...path.slice(1))
  const snap = await getDocs(constraints.length ? query(col, ...constraints) : col)
  return snap.docs.map((d) => ({ id: d.id, ...d.data() }) as T)
}

export async function fetchParticipantBundle(pid: string) {
  const [participant, responses, requests, events, devices, scoring, notes] = await Promise.all([
    getParticipant(pid),
    fetchAll<ResponseDoc & { id: string }>(['participants', pid, 'responses']),
    fetchAll<RequestDoc & { id: string }>(['participants', pid, 'requests']),
    fetchAll<EventDoc>(['participants', pid, 'events'], orderBy('ts')),
    fetchAll<DocumentData>(['participants', pid, 'devices']),
    fetchAll<DocumentData>(['participants', pid, 'scoring']),
    fetchAll<DocumentData>(['participants', pid, 'notes']),
  ])
  return { participant, responses, requests, events, devices, scoring, notes }
}

export function useServerNow() {
  return useMemo(() => serverTimestamp(), [])
}
