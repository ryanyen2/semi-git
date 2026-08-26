import type { Instrument, Item, Option } from '../study/instruments'
import type { ResponseDoc } from '../lib/types'

type Values = ResponseDoc['values']
type Value = Values[string]

interface FormProps {
  instrument: Instrument
  values: Values
  setValue: (id: string, v: Value) => void
  disabled?: boolean
  /** Item ids to highlight as missing, after a failed submit. */
  missing?: Set<string>
}

/** Item ids that are required and still unanswered. */
export function missingItems(instrument: Instrument, values: Values): Set<string> {
  const out = new Set<string>()
  for (const item of instrument.items) {
    if (!item.required) continue
    const v = values[item.id]
    if (item.type === 'grid') {
      for (const row of item.rows ?? []) {
        if (values[`${item.id}.${row.id}`] == null) out.add(item.id)
      }
      continue
    }
    if (item.type === 'checkbox') {
      if (v !== true) out.add(item.id)
      continue
    }
    if (item.type === 'multi') {
      if (!Array.isArray(v) || v.length === 0) out.add(item.id)
      continue
    }
    if (v == null || v === '') out.add(item.id)
  }
  return out
}

function Anchors({ item }: { item: Item }) {
  if (!item.anchors) return null
  return (
    <div className="anchors">
      <span>{item.anchors[0]}</span>
      <span>{item.anchors[1]}</span>
    </div>
  )
}

function LikertRow({
  item,
  value,
  onChange,
  disabled,
}: {
  item: Item
  value: Value
  onChange: (v: number) => void
  disabled?: boolean
}) {
  const min = item.min ?? 1
  const max = item.max ?? 7
  const points = Array.from({ length: max - min + 1 }, (_, i) => min + i)
  // Anchors inside the row, at its two ends, rather than a strip underneath.
  // See the `.likert-opts` rule in styles.css.
  return (
    <div className="likert">
      <div className="likert-opts" role="radiogroup" aria-label={item.label}>
        {item.anchors && <span className="likert-anchor">{item.anchors[0]}</span>}
        {points.map((p) => (
          <label key={p} className={`likert-opt${value === p ? ' on' : ''}`}>
            <input
              type="radio"
              name={item.id}
              checked={value === p}
              disabled={disabled}
              onChange={() => onChange(p)}
            />
            {p}
          </label>
        ))}
        {item.anchors && <span className="likert-anchor">{item.anchors[1]}</span>}
      </div>
    </div>
  )
}

/**
 * A slider that cannot be silently skipped.
 *
 * An HTML range always draws its thumb somewhere, so an untouched slider parked
 * at the midpoint is indistinguishable from a deliberate answer of "50". Two
 * things follow from that, and both matter:
 *
 * - Until it is touched, the value reads "not answered" and the control is
 *   dimmed, so the participant can see the difference.
 * - Any interaction commits a value, including a click on the thumb where it
 *   already sits. A change event does not fire when the value does not change,
 *   so without this a participant who genuinely wants the midpoint taps it,
 *   sees nothing happen, and is then told the question is unanswered.
 */
function SliderRow({
  item,
  value,
  onChange,
  disabled,
}: {
  item: Item
  value: Value
  onChange: (v: number) => void
  disabled?: boolean
}) {
  const min = item.min ?? 0
  const max = item.max ?? 100
  const step = item.step ?? 1
  const touched = typeof value === 'number'
  const midpoint = Math.round((min + max) / 2 / step) * step
  const shown = touched ? (value as number) : midpoint

  const commit = () => {
    if (!touched && !disabled) onChange(midpoint)
  }

  return (
    <div style={touched ? undefined : { opacity: 0.72 }}>
      <div className="row" style={{ gap: '0.75rem', flexWrap: 'nowrap' }}>
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={shown}
          disabled={disabled}
          aria-label={item.label}
          aria-valuetext={touched ? String(shown) : 'not answered yet'}
          onChange={(e) => onChange(Number(e.target.value))}
          onPointerDown={commit}
          onKeyDown={commit}
          onBlur={commit}
        />
        <span className="tlx-value" style={touched ? undefined : { color: 'var(--faint)' }}>
          {touched ? shown : '–'}
        </span>
      </div>
      <Anchors item={item} />
      {!touched && (
        <div className="tiny faint" style={{ marginTop: '0.15rem' }}>
          Not answered yet — click or drag anywhere on the line.
        </div>
      )}
    </div>
  )
}

/**
 * The NASA-TLX scale, in the form the instrument actually specifies: twenty-one
 * tick marks with twenty intervals between them, bipolar text anchors at the two
 * ends, and no number anywhere.
 *
 * It was an HTML range input with a numeric readout beside it. Three things
 * were wrong with that. A range slider invites dragging to a value, which is a
 * magnitude judgement, where TLX asks for a mark in an interval. The readout
 * turns the answer into a number the participant then reasons about ("I said 60
 * last time"), and TLX is meant to be answered on first instinct. And a slider
 * thumb sits somewhere from the moment it renders, so an untouched scale looks
 * answered -- the old code carried a whole apparatus of dimming and synthetic
 * commit events to work around exactly that. Twenty-one discrete targets have
 * no default position, so an unanswered scale is simply empty.
 *
 * The recorded value is unchanged: 0 to 100 in steps of 5. That is the
 * twenty-one TICK MARKS numbered, not the twenty intervals -- the two counts
 * describe the same scale and get conflated constantly, which is why published
 * work reports it as both 20-point and 21-point. Twenty-one is the number of
 * answers a participant can give. `tlxScore` and its tests are untouched.
 *
 * The dividers at 0, 50 and 100 are darkened, so the middle of the scale is
 * findable at a glance. Deliberately unlabelled -- a labelled midpoint reads as
 * a neutral option, which this scale does not have. The reference implementation
 * draws its landmarks as full-height tick marks against half-height ones; this
 * is a row of cells rather than ticks on a rule, so it marks the same three
 * positions the way this shape allows.
 */
function TlxScale({
  item,
  value,
  onChange,
  disabled,
}: {
  item: Item
  value: Value
  onChange: (v: number) => void
  disabled?: boolean
}) {
  const steps = Array.from({ length: 21 }, (_, i) => i * 5)
  const selected = typeof value === 'number' ? value : null
  // Performance is the one subscale whose ends run the other way, and marking it
  // in the wrong direction is this instrument's best-documented failure mode --
  // enough of one that the reference implementation sets those two anchors apart
  // from the other five on purpose, rather than trusting the words alone.
  const flagged = item.reverse ? ' flagged' : ''
  return (
    <div className="tlx">
      <span className={`tlx-anchor${flagged}`}>{item.anchors?.[0]}</span>
      <div className="tlx-track" role="radiogroup" aria-label={item.label}>
        {steps.map((v) => (
          <label
            key={v}
            className={
              `tlx-tick${selected === v ? ' on' : ''}${v % 50 === 0 ? ' landmark' : ''}`
            }
            title={item.anchors ? `${item.anchors[0]} … ${item.anchors[1]}` : undefined}
          >
            <input
              type="radio"
              name={item.id}
              checked={selected === v}
              disabled={disabled}
              aria-label={`${v} out of 100`}
              onChange={() => onChange(v)}
            />
          </label>
        ))}
      </div>
      <span className={`tlx-anchor${flagged}`}>{item.anchors?.[1]}</span>
    </div>
  )
}

function Grid({
  item,
  values,
  setValue,
  disabled,
}: {
  item: Item
  values: Values
  setValue: (id: string, v: Value) => void
  disabled?: boolean
}) {
  const opts = item.options ?? []
  return (
    <div className="scroll-x">
      <table className="matrix">
        <thead>
          <tr>
            <th />
            {opts.map((o: Option) => (
              <th key={o.value}>{o.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(item.rows ?? []).map((row) => {
            const key = `${item.id}.${row.id}`
            return (
              <tr key={row.id}>
                <td>
                  <code>{row.label}</code>
                </td>
                {opts.map((o) => (
                  <td key={o.value}>
                    <input
                      type="radio"
                      name={key}
                      aria-label={`${row.label}: ${o.label}`}
                      checked={values[key] === o.value}
                      disabled={disabled}
                      onChange={() => setValue(key, o.value)}
                    />
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Field({
  item,
  values,
  setValue,
  disabled,
  isMissing,
}: {
  item: Item
  values: Values
  setValue: (id: string, v: Value) => void
  disabled?: boolean
  isMissing: boolean
}) {
  const value = values[item.id]

  const control = (() => {
    switch (item.type) {
      case 'statement':
        return null

      case 'section':
        return null

      case 'checkbox':
        return (
          <label className={`check${value === true ? ' on' : ''}`}>
            <input
              type="checkbox"
              checked={value === true}
              disabled={disabled}
              onChange={(e) => setValue(item.id, e.target.checked)}
            />
            <span>
              {item.label}
              {item.required && <span className="req">*</span>}
            </span>
          </label>
        )

      case 'text':
        return (
          <input
            type="text"
            value={(value as string) ?? ''}
            placeholder={item.placeholder}
            disabled={disabled}
            onChange={(e) => setValue(item.id, e.target.value)}
          />
        )

      case 'number':
        return (
          <input
            type="number"
            min={item.min}
            max={item.max}
            value={value == null ? '' : String(value)}
            disabled={disabled}
            onChange={(e) => setValue(item.id, e.target.value === '' ? null : Number(e.target.value))}
          />
        )

      case 'textarea':
        return (
          <textarea
            value={(value as string) ?? ''}
            placeholder={item.placeholder}
            disabled={disabled}
            onChange={(e) => setValue(item.id, e.target.value)}
          />
        )

      case 'select':
        return (
          <div className="row tight">
            {(item.options ?? []).map((o) => (
              <button
                key={o.value}
                type="button"
                className={`btn${value === o.value ? ' primary' : ''}`}
                disabled={disabled}
                onClick={() => setValue(item.id, o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>
        )

      case 'multi': {
        const arr = Array.isArray(value) ? (value as string[]) : []
        return (
          <div className="stack tight">
            {(item.options ?? []).map((o) => {
              const on = arr.includes(o.value)
              return (
                <label key={o.value} className={`check${on ? ' on' : ''}`}>
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={disabled}
                    onChange={() =>
                      setValue(
                        item.id,
                        on ? arr.filter((x) => x !== o.value) : [...arr, o.value],
                      )
                    }
                  />
                  <span>{o.label}</span>
                </label>
              )
            })}
          </div>
        )
      }

      case 'slider':
        return (
          <SliderRow
            item={item}
            value={value}
            disabled={disabled}
            onChange={(v) => setValue(item.id, v)}
          />
        )

      case 'likert':
        return (
          <LikertRow
            item={item}
            value={value}
            disabled={disabled}
            onChange={(v) => setValue(item.id, v)}
          />
        )

      case 'tlx':
        return (
          <TlxScale
            item={item}
            value={value}
            disabled={disabled}
            onChange={(v) => setValue(item.id, v)}
          />
        )

      case 'grid':
        return <Grid item={item} values={values} setValue={setValue} disabled={disabled} />
    }
  })()

  // A block heading, not a question. Grouping the items that belong together
  // under a visible label is what stops a long column of near-identical rows
  // being answered by pattern rather than by reading.
  if (item.type === 'section') {
    return (
      <div>
        <h3 className="form-section">{item.label}</h3>
        {item.help && <p className="small muted" style={{ margin: '0.25rem 0 0' }}>{item.help}</p>}
      </div>
    )
  }

  const showLabel = item.type !== 'checkbox'

  // A TLX label carries both the subscale's name and its question, joined by an
  // em dash ("Mental demand — How mentally demanding was the task?"). The name
  // is what the participant scans for on the second and third administration,
  // so it is set apart; the question is what they should actually read the
  // first time. Splitting on the dash here rather than adding a second field to
  // `Item` keeps the instrument file readable as prose.
  const dash = item.type === 'tlx' ? item.label.indexOf(' — ') : -1
  const renderedLabel =
    dash < 0 ? (
      item.label
    ) : (
      <>
        <strong>{item.label.slice(0, dash)}</strong>
        {item.label.slice(dash)}
      </>
    )

  return (
    <div
      className="field"
      style={isMissing ? { borderLeft: '3px solid var(--bad)', paddingLeft: '0.9rem' } : undefined}
    >
      {showLabel && (
        <div className="field-label">
          {renderedLabel}
          {item.required && <span className="req">*</span>}
        </div>
      )}
      {item.help && <div className="field-help">{item.help}</div>}
      {control}
      {isMissing && (
        <div className="tiny" style={{ color: 'var(--bad)' }}>
          Still needs an answer.
        </div>
      )}
    </div>
  )
}

export function Form({ instrument, values, setValue, disabled, missing }: FormProps) {
  return (
    <div>
      {instrument.items.map((item) => (
        <Field
          key={item.id}
          item={item}
          values={values}
          setValue={setValue}
          disabled={disabled}
          isMissing={missing?.has(item.id) ?? false}
        />
      ))}
    </div>
  )
}
