import { useMemo, useState } from 'react'
import { useParticipant } from '../ParticipantApp'
import { useLiveCollection, useLiveDoc } from '../../lib/db'
import type { DeviceDoc, PublicConfig } from '../../lib/types'
import { blockFor, type Step } from '../../study/flow'
import { Callout, Copyable, fmtAgo } from '../../ui/bits'

const CHECK_LABELS: Array<[string, string]> = [
  ['uv', 'Python manager installed'],
  ['python', 'Python ready'],
  ['venv', 'Project environment ready'],
  ['smoke', 'Project runs'],
  ['tests', 'Project tests pass'],
  ['tool', 'History tool ready'],
  ['warm', 'History loaded'],
  ['tool_key', 'History tool key ready'],
  ['tool_key_live', 'History tool connected'],
  ['assistant_skill', 'Assistant skill installed'],
  ['assistant_profile', 'Assistant profile ready'],
  ['assistant_model', 'Assistant model ready'],
  ['assistant_key', 'Assistant key ready'],
  ['assistant_ping', 'Assistant connected'],
  ['assistant_model_live', 'Assistant model connected'],
  ['editor', 'Editor ready'],
  ['editor_extension', 'History view installed'],
  ['editor_toolset', 'Editor setup ready'],
  ['telemetry', 'Session logging connected'],
]

export function SetupStep({ step }: { step: Step }) {
  const { pid, participant, goNext, goBack } = useParticipant()
  const half = step.half!
  const block = blockFor(participant.blocks, half)
  const { data: cfg } = useLiveDoc<PublicConfig>(['public', 'config'])
  const { data: devices } = useLiveCollection<DeviceDoc & { id: string }>([
    'participants',
    pid,
    'devices',
  ])
  const [override, setOverride] = useState(false)

  const device = useMemo(() => {
    const forHalf = (devices ?? []).filter((d) => d.half === half)
    return forHalf.sort((a, b) => (b.lastSeenAt ?? 0) - (a.lastSeenAt ?? 0))[0] ?? null
  }, [devices, half])

  const token = block.condition === 'sgt' ? 'b' : 'a'
  const bundleUrl =
    cfg?.bundleUrls?.[`${block.condition}-${block.project}`] ||
    `/bundles/study-${block.project}-${token}.tgz`

  const command = `bash install/setup.sh ${pid}`

  const checks = device?.checks ?? {}
  const known = CHECK_LABELS.filter(([k]) => k in checks)
  const extra = Object.keys(checks).filter((k) => !CHECK_LABELS.some(([lk]) => lk === k))
  const total = known.length + extra.length
  const passing = Object.values(checks).filter((c) => c.ok).length
  const allGood = total > 0 && passing === total
  const anyBad = Object.values(checks).some((c) => !c.ok)

  return (
    <div className="stack loose">
      <div>
        <div className="eyebrow">
          {block.label} · {half === 1 ? 'first half' : 'second half'}
        </div>
        <h1>{half === 1 ? 'Set up the first project' : 'Set up the second project'}</h1>
        <p className="lede">
          Download the project, run the setup command, and wait for all checks to pass.
        </p>
      </div>

      <div className="card">
        <h2>1. Download the project</h2>
        <p className="small muted">
          Download the folder for this half and unpack it somewhere you can open from a terminal.
        </p>
        <a className="btn primary" href={bundleUrl} download>
          Download
        </a>
      </div>

      <div className="card">
        <h2>2. Run the setup</h2>
        <p className="small muted">
          Open a terminal in the unpacked folder and run:
        </p>
        <Copyable text={command} />
        <p className="small muted" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
          The first setup may take a few minutes. Keep the terminal open while it runs.
        </p>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2 style={{ margin: 0 }}>3. Wait for the checks</h2>
          {device ? (
            <span className="badge outline">
              <span className="dot live" /> {device.os} · last heard {fmtAgo(device.lastSeenAt)}
            </span>
          ) : (
            <span className="badge outline">waiting for setup</span>
          )}
        </div>

        <p className="small muted">
          The checks update automatically as setup finishes.
        </p>

        {total === 0 ? (
          <div className="empty small">
            Waiting for the setup command to report.
          </div>
        ) : (
          <div className="stack tight" style={{ marginTop: '0.75rem' }}>
            {known.map(([key, label]) => (
              <CheckRow
                key={key}
                label={label}
                ok={checks[key].ok}
                detail={checks[key].detail}
              />
            ))}
            {extra.map((key) => (
              <CheckRow
                key={key}
                label={key}
                ok={checks[key].ok}
                detail={checks[key].detail}
              />
            ))}
          </div>
        )}

        {anyBad && (
          <Callout kind="bad" title="A setup check failed">
            Tell your facilitator before continuing.
          </Callout>
        )}
      </div>

      {allGood && (
        <div
          className="card good"
          style={{ borderColor: '#b9e0c9', background: 'var(--good-soft)' }}
        >
          <h2 style={{ marginBottom: '0.35rem' }}>Ready</h2>
          <p className="small" style={{ marginBottom: '0.75rem' }}>
            Start the study shell:
          </p>
          <Copyable text="./bin/study-shell" />
          <p className="small muted" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
            Leave this shell open for the rest of this half. Run all study commands here.
          </p>
        </div>
      )}

      <div className="sticky-actions">
        <button className="btn primary lg" onClick={goNext} disabled={!allGood && !override}>
          Continue
        </button>

        <button className="btn ghost" onClick={goBack}>
          Back
        </button>

        {!allGood && (
          <label className="check" style={{ border: 0, background: 'none', padding: 0 }}>
            <input
              type="checkbox"
              checked={override}
              onChange={(e) => setOverride(e.target.checked)}
            />
            <span className="small muted">
              Continue without all checks passing
            </span>
          </label>
        )}
      </div>
    </div>
  )
}

function CheckRow({
  label,
  ok,
  detail,
}: {
  label: string
  ok: boolean
  detail: string
}) {
  return (
    <div
      className="row"
      style={{ alignItems: 'flex-start', gap: '0.6rem', flexWrap: 'nowrap' }}
    >
      <span
        className={`dot ${ok ? 'good' : 'bad'}`}
        style={{ marginTop: '0.5rem' }}
      />
      <div style={{ minWidth: 0 }}>
        <div className={ok ? '' : 'strong'}>{label}</div>
        {detail && (
          <div
            className="tiny mono"
            style={{
              color: ok ? 'var(--faint)' : 'var(--bad)',
              wordBreak: 'break-word',
            }}
          >
            {detail}
          </div>
        )}
      </div>
    </div>
  )
}