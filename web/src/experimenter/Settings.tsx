import { useEffect, useRef, useState } from 'react'
import { deleteDoc, doc, setDoc } from 'firebase/firestore'
import { db } from '../lib/firebase'
import { useLiveCollection, useLiveDoc } from '../lib/db'
import type { GroundTruth, PublicConfig, SecretsDoc } from '../lib/types'
import { validateGroundTruth } from '../study/answerKey'
import { Callout, Copyable, fmtAgo } from '../ui/bits'
import { OWNER_EMAIL } from './ExperimenterApp'

const BUNDLE_KEYS = [
  ['git-coursecraft', 'Git · coursecraft'],
  ['sgt-coursecraft', 'sgt · coursecraft'],
  ['git-confplan', 'Git · confplan'],
  ['sgt-confplan', 'sgt · confplan'],
] as const

export function Settings() {
  return (
    <div className="stack loose">
      <div>
        <h1 style={{ marginBottom: '0.15rem' }}>Setup</h1>
        <p className="muted small" style={{ margin: 0 }}>
          Do all of this once, before participant one.
        </p>
      </div>
      <Credentials />
      <PublicSettings />
      <GroundTruthPanel />
      <Experimenters />
    </div>
  )
}

function Experimenters() {
  const { data: admins } = useLiveCollection<{ id: string; addedAt?: number }>(['admins'])
  const [email, setEmail] = useState('')

  async function add() {
    const clean = email.trim().toLowerCase()
    if (!clean.includes('@')) return
    await setDoc(doc(db, 'admins', clean), { role: 'experimenter', addedAt: Date.now() })
    setEmail('')
  }

  return (
    <div className="card">
      <h2>Who else can see this console</h2>
      <p className="small muted">
        Anyone here can read every participant's data, including the answer key. The study owner is
        named in the security rules and cannot be removed from this page.
      </p>
      <div className="stack tight" style={{ margin: '0.75rem 0' }}>
        <div className="row tight">
          <span className="badge good">{OWNER_EMAIL}</span>
          <span className="tiny muted">owner, set in firestore.rules</span>
        </div>
        {(admins ?? []).map((a) => (
          <div key={a.id} className="row tight">
            <span className="badge outline">{a.id}</span>
            <button className="btn sm danger" onClick={() => void deleteDoc(doc(db, 'admins', a.id))}>
              Remove
            </button>
          </div>
        ))}
      </div>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault()
          void add()
        }}
      >
        <input
          type="email"
          value={email}
          placeholder="colleague@example.org"
          onChange={(e) => setEmail(e.target.value)}
          style={{ maxWidth: '22rem' }}
        />
        <button className="btn" type="submit" disabled={!email.includes('@')}>
          Add experimenter
        </button>
      </form>
    </div>
  )
}

function Credentials() {
  const { data } = useLiveDoc<SecretsDoc>(['study', 'credentials'])
  const [openai, setOpenai] = useState('')
  const [anthropic, setAnthropic] = useState('')
  const [model, setModel] = useState('claude-sonnet-5')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (!data) return
    setOpenai(data.openaiApiKey ?? '')
    setAnthropic(data.anthropicApiKey ?? '')
    setModel(data.claudeModel ?? 'claude-sonnet-5')
  }, [data?.issuedAt])

  async function save() {
    await setDoc(
      doc(db, 'study', 'credentials'),
      {
        openaiApiKey: openai.trim(),
        anthropicApiKey: anthropic.trim(),
        claudeModel: model.trim(),
        issuedAt: Date.now(),
        revokedAt: null,
      } satisfies SecretsDoc,
      { merge: true },
    )
    setSaved(true)
    window.setTimeout(() => setSaved(false), 1500)
  }

  return (
    <div className="card">
      <h2>Session keys</h2>
      <p className="small muted">
        Issued once here, copied into each participant record, and fetched by the setup script using
        their code. The participant never sees or pastes a key, and the assistant runs from a
        profile inside the study folder, so their own account and billing are untouched.
      </p>

      <Callout kind="warn" title="Issue keys for the study, cap them, and revoke on the last day">
        These are readable by anyone holding a participant link, which is the price of the setup
        script fetching them automatically. Use keys created for this study with a hard spend cap,
        never a personal key. Revoke per participant from the Participants tab as each session
        ends.
      </Callout>

      <div className="field">
        <div className="field-label">Anthropic API key</div>
        <div className="field-help">Used by Claude Code, through an isolated profile.</div>
        <input
          type="text"
          value={anthropic}
          onChange={(e) => setAnthropic(e.target.value)}
          placeholder="sk-ant-..."
          className="mono"
        />
      </div>
      <div className="field">
        <div className="field-label">OpenAI API key</div>
        <div className="field-help">Used by sgt for plain-English selections and feature naming.</div>
        <input
          type="text"
          value={openai}
          onChange={(e) => setOpenai(e.target.value)}
          placeholder="sk-..."
          className="mono"
        />
      </div>
      <div className="field">
        <div className="field-label">Model, pinned for every session</div>
        <div className="field-help">
          Part of the condition. Changing it mid-study makes the two groups incomparable, so it is
          recorded with every participant.
        </div>
        <input type="text" value={model} onChange={(e) => setModel(e.target.value)} className="mono" />
      </div>

      <div className="row">
        <button className="btn primary" onClick={() => void save()}>
          Save keys
        </button>
        {saved && <span className="savechip"><span className="dot good" /> saved</span>}
        {data?.issuedAt && <span className="small muted">last set {fmtAgo(data.issuedAt)}</span>}
      </div>
    </div>
  )
}

function PublicSettings() {
  const { data } = useLiveDoc<PublicConfig>(['public', 'config'])
  const [cfg, setCfg] = useState<PublicConfig>({
    studyTitle: 'Working with project history',
    supportEmail: '',
    bundleUrls: {},
    consentBodyMarkdown: '',
    compensation: '',
    irbProtocol: '',
    active: true,
  })
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (data) setCfg({ ...cfg, ...data })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  async function save() {
    await setDoc(doc(db, 'public', 'config'), cfg, { merge: true })
    setSaved(true)
    window.setTimeout(() => setSaved(false), 1500)
  }

  return (
    <div className="card">
      <h2>Participant-facing settings</h2>

      <div className="field">
        <div className="field-label">Support email</div>
        <input
          type="email"
          value={cfg.supportEmail}
          onChange={(e) => setCfg({ ...cfg, supportEmail: e.target.value })}
        />
      </div>

      <div className="field">
        <div className="field-label">Compensation, as the participant should read it</div>
        <input
          type="text"
          value={cfg.compensation}
          onChange={(e) => setCfg({ ...cfg, compensation: e.target.value })}
          placeholder="You will be paid $60 for the session, whether or not you finish."
        />
      </div>

      <div className="field">
        <div className="field-label">Protocol number</div>
        <input
          type="text"
          value={cfg.irbProtocol}
          onChange={(e) => setCfg({ ...cfg, irbProtocol: e.target.value })}
          placeholder="IRB-2026-…"
        />
      </div>

      <div className="field">
        <div className="field-label">Bundle download links</div>
        <div className="field-help">
          <strong>Normally leave these blank.</strong> <code>make-study-bundle.sh</code> writes into
          the site's own static files, so a deployed bundle is already served at{' '}
          <code>/bundles/study-&lt;project&gt;-&lt;a|b&gt;.tgz</code>, which is where the setup page
          looks. Fill one in only to host that bundle somewhere else.
        </div>
        <div className="stack tight">
          {BUNDLE_KEYS.map(([key, label]) => (
            <div key={key} className="row" style={{ flexWrap: 'nowrap' }}>
              <span className="small muted" style={{ width: '11rem', flex: 'none' }}>
                {label}
              </span>
              <input
                type="text"
                value={cfg.bundleUrls?.[key] ?? ''}
                placeholder="https://…"
                onChange={(e) =>
                  setCfg({ ...cfg, bundleUrls: { ...cfg.bundleUrls, [key]: e.target.value } })
                }
              />
            </div>
          ))}
        </div>
      </div>

      <div className="field">
        <div className="field-label">Consent information sheet</div>
        <div className="field-help">
          Markdown. Shown above the consent checkboxes. Leave blank to use the built-in text, which
          is accurate but not approved by anybody.
        </div>
        <textarea
          value={cfg.consentBodyMarkdown}
          onChange={(e) => setCfg({ ...cfg, consentBodyMarkdown: e.target.value })}
          className="tall"
        />
      </div>

      <div className="row">
        <button className="btn primary" onClick={() => void save()}>
          Save
        </button>
        {saved && <span className="savechip"><span className="dot good" /> saved</span>}
      </div>
    </div>
  )
}

function GroundTruthPanel() {
  const { data } = useLiveDoc<GroundTruth>(['study', 'groundTruth'])
  const fileRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)

  async function load(file: File) {
    setError(null)
    try {
      const parsed = JSON.parse(await file.text()) as GroundTruth
      validateGroundTruth(parsed)
      await setDoc(doc(db, 'study', 'groundTruth'), parsed)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="card">
      <h2>Answer key</h2>
      <p className="small muted">
        Episodes, request keys and rubrics. Stored where only signed-in experimenters can read it,
        and deliberately not compiled into the site, so a participant with devtools open cannot read
        the answers to request 1's questions out of the JavaScript.
      </p>

      <div className="row">
        <button className="btn primary" onClick={() => fileRef.current?.click()}>
          Load answer key JSON
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void load(f)
            e.target.value = ''
          }}
        />
        {data ? (
          <span className="badge good">
            loaded · {data.episodes?.length ?? 0} episodes · version {data.version}
          </span>
        ) : (
          <span className="badge warn">not loaded</span>
        )}
      </div>

      {error && (
        <p className="small" style={{ color: 'var(--bad)' }}>
          {error}
        </p>
      )}

      <p className="small muted" style={{ marginTop: '1rem', marginBottom: '0.4rem' }}>
        The file lives in the repository:
      </p>
      <Copyable text="docs/study/answer-key.json" />
    </div>
  )
}
