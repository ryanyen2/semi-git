import { useMemo, useState } from 'react'
import { doc, orderBy, setDoc } from 'firebase/firestore'
import { db } from '../lib/firebase'
import {
  PILOT_ORDINAL_BASE,
  createCohort,
  deleteParticipantDeep,
  isPilot,
  participantFootprint,
  patchParticipant,
  resetParticipant,
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
              <strong>Reset</strong> and <strong>Delete</strong> for one participant live on their
              own row, where you can see who you are acting on. This is the bulk version, for
              clearing test data before the study starts.
            </p>
            <p className="small">
              Both remove everything underneath a participant — responses, requests, events,
              devices, keys, scores and notes — not just the record. There is no undo and no
              export first.
            </p>
            <label className="check" style={{ border: 0, background: 'none', padding: 0 }}>
              <input
                type="checkbox"
                checked={confirmReset}
                onChange={(e) => setConfirmReset(e.target.checked)}
              />
              <span className="small">I understand, show the bulk actions</span>
            </label>
            {confirmReset && <BulkPurge all={all} rows={rows} pilots={pilots} />}
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
        <div className="row tight">
          <button className="btn sm" onClick={() => onOpen(p.code)}>
            Open
          </button>
          <RowActions p={p} />
        </div>
      </td>
    </tr>
  )
}

/**
 * Reset and Delete for one participant, on their own row.
 *
 * They were in a bulk "danger zone" gated on `status === 'created'`, which
 * meant the moment somebody opened their link they became untouchable -- so
 * P13, a test record that had reached `consented`, could not be cleared at all
 * and looked like the roster was hardcoded to twelve. Two changes: every record
 * is actionable whatever its status, and the action names the person and states
 * what it will destroy before it does it.
 */
function RowActions({ p }: { p: Participant & { id: string } }) {
  const [pending, setPending] = useState<null | 'reset' | 'delete'>(null)
  const [footprint, setFootprint] = useState<Record<string, number> | null>(null)
  const [busy, setBusy] = useState(false)

  async function arm(kind: 'reset' | 'delete') {
    setPending(kind)
    setFootprint(null)
    // Counted at confirm time, not on render: this is one read per
    // subcollection per participant, and doing it for every row on every
    // roster paint would be a dozen reads a second for a number nobody is
    // looking at yet.
    setFootprint(await participantFootprint(p.code))
  }

  async function go() {
    setBusy(true)
    try {
      if (pending === 'reset') await resetParticipant(p.code)
      else await deleteParticipantDeep(p.code)
      setPending(null)
    } finally {
      setBusy(false)
    }
  }

  if (!pending) {
    return (
      <>
        <button
          className="btn sm"
          title={`Wipe ${p.label}'s data but keep their link and their condition order`}
          onClick={() => void arm('reset')}
        >
          Reset
        </button>
        <button
          className="btn sm danger"
          title={`Remove ${p.label} and everything underneath them`}
          onClick={() => void arm('delete')}
        >
          Delete
        </button>
      </>
    )
  }

  const total = footprint ? Object.values(footprint).reduce((a, b) => a + b, 0) : null
  const detail = footprint
    ? Object.entries(footprint).map(([k, n]) => `${n} ${k}`).join(', ')
    : 'counting…'

  return (
    <div className="stack tight" style={{ minWidth: '15rem' }}>
      <span className="small">
        {pending === 'reset' ? (
          <>
            Reset <strong>{p.label}</strong> to step one. Their link and condition order stay;
            everything they did is deleted.
          </>
        ) : (
          <>
            Delete <strong>{p.label}</strong> entirely. Their link stops working.
          </>
        )}
      </span>
      <span className="tiny faint">
        {total === 0 ? 'Nothing recorded yet.' : `This destroys: ${detail}.`}
      </span>
      <div className="row tight">
        <button className="btn sm danger" disabled={busy || footprint === null} onClick={() => void go()}>
          {busy ? 'Working' : pending === 'reset' ? `Reset ${p.label}` : `Delete ${p.label}`}
        </button>
        <button className="btn sm" disabled={busy} onClick={() => setPending(null)}>
          Cancel
        </button>
      </div>
    </div>
  )
}

/**
 * Clearing test data before the study starts -- the bulk case the per-row
 * buttons make tedious. Deliberately offers "pilots only" first: that is the
 * safe, common intent, and putting it beside "everything" makes the difference
 * visible at the moment of choosing rather than in a sentence above.
 */
function BulkPurge({
  all,
  rows,
  pilots,
}: {
  all: (Participant & { id: string })[]
  rows: (Participant & { id: string })[]
  pilots: (Participant & { id: string })[]
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [armed, setArmed] = useState<string | null>(null)

  async function purge(label: string, targets: (Participant & { id: string })[]) {
    setBusy(label)
    setDone(null)
    try {
      let removed = 0
      // Sequential, not Promise.all: each participant is many batched writes,
      // and firing a dozen of those at once is how you get the client
      // rate-limited halfway through a delete with no record of where it got to.
      for (const p of targets) removed += await deleteParticipantDeep(p.code)
      setDone(`Deleted ${targets.length} participant(s) and ${removed} sub-document(s).`)
      setArmed(null)
    } finally {
      setBusy(null)
    }
  }

  const options: [string, (Participant & { id: string })[]][] = [
    ['pilots', pilots],
    ['real participants', rows],
    ['everyone', all],
  ]

  return (
    <div className="stack tight" style={{ marginTop: '0.75rem' }}>
      <div className="row tight">
        {options.map(([label, targets]) => (
          <button
            key={label}
            className="btn sm danger"
            disabled={busy !== null || targets.length === 0}
            onClick={() => setArmed(armed === label ? null : label)}
          >
            Delete all {label} ({targets.length})
          </button>
        ))}
      </div>
      {armed && (
        <div className="row tight">
          <span className="small">
            Permanently delete{' '}
            <strong>
              {options.find(([l]) => l === armed)?.[1].length} {armed}
            </strong>{' '}
            and everything underneath them?
          </span>
          <button
            className="btn sm danger"
            disabled={busy !== null}
            onClick={() => void purge(armed, options.find(([l]) => l === armed)?.[1] ?? [])}
          >
            {busy ? 'Deleting' : 'Yes, delete'}
          </button>
          <button className="btn sm" disabled={busy !== null} onClick={() => setArmed(null)}>
            Cancel
          </button>
        </div>
      )}
      {done && <span className="small">{done}</span>}
    </div>
  )
}
