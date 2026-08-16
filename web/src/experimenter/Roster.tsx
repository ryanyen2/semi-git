import { useMemo, useState } from 'react'
import { doc, orderBy, setDoc, deleteDoc } from 'firebase/firestore'
import { db } from '../lib/firebase'
import {
  PILOT_ORDINAL_BASE,
  createCohort,
  isPilot,
  patchParticipant,
  useLiveCollection,
  useLiveDoc,
} from '../lib/db'
import type { Participant, SecretsDoc, StudyId } from '../lib/types'

/** Position within a record's own study -- pilots are stored in a 1000+ band. */
const seqOf = (p: Participant) =>
  isPilot(p) ? p.ordinal - PILOT_ORDINAL_BASE : p.ordinal
import { Callout, Copyable, Empty, fmtAgo } from '../ui/bits'
import { downloadCsv } from '../lib/svgExport'

const STATUS_TONE: Record<string, string> = {
  created: 'outline',
  claimed: 'accent',
  consented: 'accent',
  'in-progress': 'warn',
  completed: 'good',
  withdrawn: 'bad',
  excluded: 'bad',
}

export function Roster({ onOpen }: { onOpen: (pid: string) => void }) {
  const { data: participants, loading } = useLiveCollection<Participant & { id: string }>(
    ['participants'],
    orderBy('ordinal'),
  )
  const { data: defaults } = useLiveDoc<SecretsDoc & { studyId?: string }>(['study', 'credentials'])
  const [busy, setBusy] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)

  const all = participants ?? []
  // Split once, at the top, and never re-mix. Every count, every balance
  // figure and every export below reads one of these two lists, so a pilot
  // cannot leak into a number that describes the cohort.
  const rows = useMemo(() => all.filter((p) => !isPilot(p)), [all])
  const pilots = useMemo(() => all.filter(isPilot), [all])
  const balance = useMemo(() => {
    const byGroup = new Map<number, number>()
    for (const p of rows) byGroup.set(p.group, (byGroup.get(p.group) ?? 0) + 1)
    return [1, 2, 3, 4].map((g) => ({ group: g, n: byGroup.get(g) ?? 0 }))
  }, [rows])
  // A real record stops being deletable the moment it is opened, because after
  // that it is the only thing tying a person to their data. A pilot has no
  // person and nothing downstream, so it stays deletable for good.
  const deletable = useMemo(
    () => all.filter((p) => isPilot(p) || p.status === 'created'),
    [all],
  )

  async function create(n: number, studyId: StudyId = 'main') {
    setBusy(true)
    try {
      // Each study numbers itself. `Create 12` therefore produces P01..P12 no
      // matter how many pilots have been run first, which is the property that
      // makes rehearsing safe: the cohort's identity does not depend on how
      // much testing happened before it.
      const within = studyId === 'pilot' ? pilots : rows
      const start = within.length ? Math.max(...within.map(seqOf)) + 1 : 1
      const made = await createCohort(studyId, n, start)
      if (defaults?.anthropicApiKey || defaults?.openaiApiKey) {
        await Promise.all(made.map((p) => issueKeys(p.code, defaults)))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack loose">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ marginBottom: '0.15rem' }}>Participants</h1>
          <p className="muted small" style={{ margin: 0 }}>
            {rows.length} created · groups {balance.map((b) => `${b.group}:${b.n}`).join('  ')}
            {pilots.length > 0 && ` · ${pilots.length} pilot, not counted here`}
          </p>
        </div>
        <div className="row tight">
          <button className="btn primary" disabled={busy} onClick={() => void create(12)}>
            Create 12
          </button>
          <button className="btn" disabled={busy} onClick={() => void create(1)}>
            Add one
          </button>
          <button className="btn" disabled={busy} onClick={() => void create(1, 'pilot')}>
            Add pilot
          </button>
          <button
            className="btn"
            disabled={rows.length === 0}
            onClick={() =>
              downloadCsv(
                rows.map((p) => ({
                  label: p.label,
                  code: p.code,
                  link: `${location.origin}/p/${p.code}`,
                  group: p.group,
                  half1: `${p.blocks[0].condition}/${p.blocks[0].project}`,
                  half2: `${p.blocks[1].condition}/${p.blocks[1].project}`,
                  email: p.email ?? '',
                  status: p.status,
                })),
                'participants.csv',
              )
            }
          >
            Export CSV
          </button>
        </div>
      </div>

      {!defaults?.anthropicApiKey && (
        <Callout kind="warn" title="No session keys set yet">
          Add them under <strong>Setup</strong> before the first session. Without them the setup
          script cannot configure the assistant and the participant will be asked to log in with
          their own account, which is exactly what we are avoiding.
        </Callout>
      )}

      {loading ? (
        <Empty>Loading</Empty>
      ) : rows.length === 0 ? (
        <div className="card">
          <Empty>
            <p>No participants yet.</p>
            <p className="small">
              Create twelve and they are assigned round-robin across the four counterbalancing
              groups, so any prefix of the cohort is still balanced. Use <strong>Add pilot</strong>{' '}
              to rehearse first — pilots run the identical flow and are kept out of the analysis.
            </p>
          </Empty>
        </div>
      ) : (
        <ParticipantTable rows={rows} defaults={defaults} onOpen={onOpen} />
      )}

      {pilots.length > 0 && (
        <div className="stack">
          <div>
            <h2 style={{ marginBottom: '0.15rem' }}>Pilot records</h2>
            <p className="muted small" style={{ margin: 0 }}>
              Rehearsals. Identical flow, real bundles, real telemetry — excluded from Results and
              from the counts above. Delete them or leave them; either way they never reach the
              paper.
            </p>
          </div>
          <ParticipantTable rows={pilots} defaults={defaults} onOpen={onOpen} />
        </div>
      )}

      {all.length > 0 && (
        <details>
          <summary className="small muted" style={{ cursor: 'pointer' }}>
            Danger zone
          </summary>
          <div className="card bad" style={{ marginTop: '0.75rem' }}>
            <p className="small">
              Deleting a participant removes their record but not their responses, requests or
              events, which live in subcollections. For a <strong>real</strong> participant this is
              offered only before their session starts — after that the record is the only thing
              tying those subcollections to a person. A <strong>pilot</strong> can be deleted at any
              time, because nothing downstream reads it.
            </p>
            <label className="check" style={{ border: 0, background: 'none', padding: 0 }}>
              <input
                type="checkbox"
                checked={confirmReset}
                onChange={(e) => setConfirmReset(e.target.checked)}
              />
              <span className="small">I understand, show the delete buttons</span>
            </label>
            {confirmReset && (
              <div className="row tight" style={{ marginTop: '0.75rem' }}>
                {deletable.map((p) => (
                  <button
                    key={p.code}
                    className="btn sm danger"
                    onClick={() => void deleteDoc(doc(db, 'participants', p.code))}
                  >
                    Delete {p.label}
                  </button>
                ))}
                {deletable.length === 0 && (
                  <span className="small muted">
                    Nothing is deletable: every record has been opened and none is a pilot.
                  </span>
                )}
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  )
}

function ParticipantTable({
  rows,
  defaults,
  onOpen,
}: {
  rows: (Participant & { id: string })[]
  defaults: (SecretsDoc & { studyId?: string }) | null
  onOpen: (pid: string) => void
}) {
  return (
    <div className="card flush">
      <div className="scroll-x">
        <table className="table">
          <thead>
            <tr>
              <th>Who</th>
              <th>Group</th>
              <th>First half</th>
              <th>Second half</th>
              <th>Email</th>
              <th>Link</th>
              <th>Status</th>
              <th>Keys</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <Row key={p.code} p={p} defaults={defaults} onOpen={onOpen} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

async function issueKeys(pid: string, defaults: Partial<SecretsDoc> | null) {
  await setDoc(doc(db, 'participants', pid, 'secrets', 'session'), {
    openaiApiKey: defaults?.openaiApiKey ?? '',
    anthropicApiKey: defaults?.anthropicApiKey ?? '',
    claudeModel: defaults?.claudeModel ?? 'claude-sonnet-5',
    issuedAt: Date.now(),
    revokedAt: null,
  } satisfies SecretsDoc)
}

function Row({
  p,
  defaults,
  onOpen,
}: {
  p: Participant & { id: string }
  defaults: (SecretsDoc & { studyId?: string }) | null
  onOpen: (pid: string) => void
}) {
  const { data: secrets } = useLiveDoc<SecretsDoc>(['participants', p.code, 'secrets', 'session'])
  const [email, setEmail] = useState(p.email ?? '')
  const link = `${location.origin}/p/${p.code}`

  return (
    <tr>
      <td>
        <button className="btn sm ghost" onClick={() => onOpen(p.code)}>
          <strong>{p.label}</strong>
        </button>
        {isPilot(p) && (
          <div className="tiny faint" title="Rehearsal record — excluded from Results">
            pilot
          </div>
        )}
      </td>
      <td className="tabular">{p.group}</td>
      <td className="small">
        <span className="badge outline">{p.blocks[0].label}</span> {p.blocks[0].condition} ·{' '}
        {p.blocks[0].project}
      </td>
      <td className="small">
        <span className="badge outline">{p.blocks[1].label}</span> {p.blocks[1].condition} ·{' '}
        {p.blocks[1].project}
      </td>
      <td>
        <input
          type="email"
          value={email}
          // Named for its own row. Twelve identical `name@example.org` boxes in a tall table is
          // how the rehearsal nearly filed one participant's address against another: nothing
          // links the box under the cursor back to the label at the far left of the row. No mail
          // is sent from here -- links are handed out by hand -- so the cost of getting it wrong
          // is not a misdirected email but a wrong row in the record that consents, payment and
          // the participant's own data are matched on afterwards.
          placeholder={`${p.label} email`}
          title={`Email address on file for ${p.label} (${p.group})`}
          aria-label={`Email address for ${p.label}`}
          onChange={(e) => setEmail(e.target.value)}
          onBlur={() => {
            if ((p.email ?? '') !== email) void patchParticipant(p.code, { email: email || null })
          }}
          style={{ minWidth: '13rem', fontSize: '0.85rem' }}
        />
      </td>
      <td style={{ minWidth: '15rem' }}>
        <Copyable text={link} label={`/p/${p.code.slice(0, 8)}…`} />
      </td>
      <td>
        <span className={`badge ${STATUS_TONE[p.status] ?? 'outline'}`}>{p.status}</span>
        {p.lastSeenAt && <div className="tiny faint">{fmtAgo(p.lastSeenAt)}</div>}
      </td>
      <td>
        {secrets && !secrets.revokedAt ? (
          <button
            className="btn sm danger"
            title="Mark the keys revoked. Actually revoke them at the provider too."
            onClick={() =>
              void setDoc(
                doc(db, 'participants', p.code, 'secrets', 'session'),
                { revokedAt: Date.now(), openaiApiKey: '', anthropicApiKey: '' },
                { merge: true },
              )
            }
          >
            Revoke
          </button>
        ) : (
          <button className="btn sm" onClick={() => void issueKeys(p.code, defaults)}>
            {secrets ? 'Re-issue' : 'Issue'}
          </button>
        )}
      </td>
      <td>
        <button className="btn sm" onClick={() => onOpen(p.code)}>
          Open
        </button>
      </td>
    </tr>
  )
}
