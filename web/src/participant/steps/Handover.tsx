import { useMemo } from 'react'
import { useParticipant } from '../ParticipantApp'
import { useLiveCollection } from '../../lib/db'
import type { DeviceDoc } from '../../lib/types'
import { Callout, Copyable, fmtAgo } from '../../ui/bits'
import { Markdown } from '../../ui/Markdown'
import { HANDOVER_MD } from '../../study/content'

export function HandoverStep() {
  const { pid, goNext, goBack } = useParticipant()
  const { data: devices } = useLiveCollection<DeviceDoc & { id: string }>([
    'participants',
    pid,
    'devices',
  ])

  const summary = useMemo(() => {
    const ds = devices ?? []
    return {
      count: ds.length,
      uploaded: ds.reduce((n, d) => n + (d.eventsUploaded ?? 0), 0),
      lastSeen: ds.reduce((n, d) => Math.max(n, d.lastSeenAt ?? 0), 0),
      halves: new Set(ds.map((d) => d.half).filter(Boolean)).size,
    }
  }, [devices])

  const bothHalves = summary.halves >= 2
  const anything = summary.uploaded > 0

  return (
    <div className="stack loose">
      <div>
        <h1>Hand over your data</h1>
        <p className="lede">
          Two commands and you are finished. Both run in the session shell.
        </p>
      </div>

      <div className="card">
        <Markdown>{HANDOVER_MD}</Markdown>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2 style={{ margin: 0 }}>What we have so far</h2>
          <span className="badge outline">last heard {fmtAgo(summary.lastSeen || null)}</span>
        </div>
        <dl className="kv" style={{ marginTop: '0.75rem' }}>
          <dt>Machines reporting</dt>
          <dd className="tabular">{summary.count}</dd>
          <dt>Halves covered</dt>
          <dd className="tabular">
            {summary.halves} of 2 {bothHalves ? '' : '· one still missing'}
          </dd>
          <dt>Records received</dt>
          <dd className="tabular">{summary.uploaded.toLocaleString()}</dd>
        </dl>

        {anything && bothHalves ? (
          <Callout kind="accent" title="That is everything we need">
            You can run <code>study-cleanup</code> now.
          </Callout>
        ) : (
          <Callout kind="warn" title="Still waiting on something">
            Run <code>study-sync --final</code> in each of the two shells you used. If it still shows
            a gap after that, tell your facilitator and leave the folders in place. The log on your
            disk is intact and we can collect it another way.
          </Callout>
        )}
      </div>

      <div className="card soft">
        <div className="strong" style={{ marginBottom: '0.5rem' }}>Then remove the folders</div>
        <Copyable text="study-cleanup" />
      </div>

      <div className="sticky-actions">
        <button className="btn primary lg" onClick={goNext}>
          Done
        </button>
        <button className="btn ghost" onClick={goBack}>
          Back
        </button>
      </div>
    </div>
  )
}
