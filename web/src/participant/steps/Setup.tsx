import { useMemo, useState } from 'react'
import { useParticipant } from '../ParticipantApp'
import { useLiveCollection, useLiveDoc } from '../../lib/db'
import type { DeviceDoc, PublicConfig } from '../../lib/types'
import { blockFor, type Step } from '../../study/flow'
import { Callout, Copyable, fmtAgo } from '../../ui/bits'

// Order and wording for the checks the doctor reports. Anything the doctor
// sends that is not listed here still shows, at the end, under its raw key --
// a check that exists but has no label is better than a check that silently
// vanishes because the two sides drifted.
const CHECK_LABELS: Array<[string, string]> = [
  ['uv', 'Python manager installed'],
  ['python', 'Python 3.12 ready'],
  ['venv', 'Project environment built'],
  // `tests` is what the pytest-era doctor reported. The protocol v2 testbeds
  // have no test suite, so the check is now the project's own smoke check; the
  // old label stays so pilot records still read as words rather than a raw key.
  ['smoke', 'Project runs and every page renders'],
  ['tests', 'Project test suite passes'],
  ['tool', 'History tool ready'],
  ['warm', 'History view already loaded'],
  ['tool_key', 'History tool key in place'],
  ['tool_key_live', 'History tool key actually works'],
  ['assistant_skill', 'Assistant skill installed'],
  ['assistant_profile', 'Assistant using the session profile, not yours'],
  ['assistant_model', 'Assistant pinned to the study model'],
  ['assistant_key', 'Assistant key in place'],
  ['assistant_ping', 'Assistant answered a test message'],
  ['assistant_model_live', 'Assistant answered on the study model'],
  ['editor', 'Editor ready'],
  ['editor_extension', 'Editor history view installed'],
  ['editor_toolset', 'Editor set up the same as the other half'],
  ['telemetry', 'Session logging is uploading'],
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

  // Bundles are built straight into the site's static files, so the ordinary
  // case needs no configuration at all. The override exists for hosting them
  // somewhere else, not as the normal path: a link that has to be pasted in
  // before the first session is a link that can be missing during it.
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
        <h1>{half === 1 ? 'Set up your machine' : 'Set up the second project'}</h1>
        <p className="lede">
          {half === 1
            ? 'Three commands. Everything lands inside one folder, uses its own Python, and leaves the rest of your machine alone.'
            : 'Same three commands, on the second folder. It will be quicker this time because the Python is already downloaded.'}
        </p>
      </div>

      <Callout kind="accent" title="Nothing here touches your own setup">
        Everything runs from inside the study folder, on keys we issue for this session and revoke
        afterwards. Your own login, your own settings, and your own billing are not read and not
        used. Removing the folder at the end removes all of it.
      </Callout>

      {half === 1 && (
        <Callout kind="soft" title="If you brought a repository of your own">
          Tell your facilitator now. With the consent line ticked, they start building its history
          view in the background while you work, so it is ready for the interview at the end.
        </Callout>
      )}

      <div className="card">
        <h2>1. Download the folder for this half</h2>
        <p className="small muted">
          A few megabytes. Put it somewhere you can find in a terminal.
        </p>
        <a className="btn primary" href={bundleUrl} download>
          Download
        </a>
      </div>

      <div className="card">
        <h2>2. Unpack it and run one command</h2>
        <p className="small muted">
          Open a terminal, move into the folder you unpacked, and run this. It takes a few minutes
          the first time. It prints what it is doing.
        </p>
        <Copyable text={command} />
        <p className="small muted" style={{ marginTop: '0.75rem' }}>
          That code at the end is yours. It tells the setup which project you are on and fetches the
          keys for the session, so you never have to paste a key by hand.
        </p>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2 style={{ margin: 0 }}>3. Watch it go green</h2>
          {device ? (
            <span className="badge outline">
              <span className="dot live" /> {device.os} · last heard {fmtAgo(device.lastSeenAt)}
            </span>
          ) : (
            <span className="badge outline">waiting for your machine</span>
          )}
        </div>
        <p className="small muted">
          This list fills in by itself as the setup runs. You do not need to tell us anything.
        </p>

        {total === 0 ? (
          <div className="empty small">
            Nothing has reported yet. This appears within a few seconds of the command starting.
          </div>
        ) : (
          <div className="stack tight" style={{ marginTop: '0.75rem' }}>
            {known.map(([key, label]) => (
              <CheckRow key={key} label={label} ok={checks[key].ok} detail={checks[key].detail} />
            ))}
            {extra.map((key) => (
              <CheckRow key={key} label={key} ok={checks[key].ok} detail={checks[key].detail} />
            ))}
          </div>
        )}

        {anyBad && (
          <Callout kind="bad" title="Something did not pass">
            Show this list to your facilitator before going on. Carrying on with a red line usually
            means losing a request later, and it is much cheaper to fix now.
          </Callout>
        )}
      </div>

      {allGood && (
        <div className="card good" style={{ borderColor: '#b9e0c9', background: 'var(--good-soft)' }}>
          <h2 style={{ marginBottom: '0.35rem' }}>Ready</h2>
          <p className="small" style={{ marginBottom: '0.75rem' }}>
            Open the session shell and leave it open for the rest of the half. Everything you run
            for the study goes in here.
          </p>
          <Copyable text="./bin/study-shell" />
          <p className="small muted" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
            It opens in the project folder, <code>work/</code>. Inside it,{' '}
            <code>study-code</code> opens the project in the editor.
          </p>
          <p className="small muted" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
            Please open the editor with <code>study-code</code> rather than your own. It uses a
            separate profile inside this folder, so your own editor, extensions and settings are
            left exactly as they are.
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
            <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />
            <span className="small muted">
              My facilitator says to carry on anyway
            </span>
          </label>
        )}
      </div>
    </div>
  )
}

function CheckRow({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    // `flexWrap: nowrap` with `minWidth: 0` on the text column, because the
    // details are things like absolute paths and pytest output: one long
    // unbreakable token would otherwise push the label onto its own line and
    // leave the status dot floating above nothing.
    <div className="row" style={{ alignItems: 'flex-start', gap: '0.6rem', flexWrap: 'nowrap' }}>
      <span className={`dot ${ok ? 'good' : 'bad'}`} style={{ marginTop: '0.5rem' }} />
      <div style={{ minWidth: 0 }}>
        <div className={ok ? '' : 'strong'}>{label}</div>
        {detail && (
          <div
            className="tiny mono"
            style={{ color: ok ? 'var(--faint)' : 'var(--bad)', wordBreak: 'break-word' }}
          >
            {detail}
          </div>
        )}
      </div>
    </div>
  )
}
