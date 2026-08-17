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
  return (
    <div className="likert">
      <div className="likert-opts" role="radiogroup" aria-label={item.label}>
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
      </div>
      <Anchors item={item} />
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
  ticks,
}: {
  item: Item
  value: Value
  onChange: (v: number) => void
  disabled?: boolean
  ticks?: number
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
      {ticks ? (
        <div className="ticks" aria-hidden>
          {Array.from({ length: ticks }, (_, i) => (
            <span key={i} />
          ))}
        </div>
      ) : null}
      <Anchors item={item} />
      {!touched && (
        <div className="tiny faint" style={{ marginTop: '0.15rem' }}>
          Not answered yet — click or drag anywhere on the line.
        </div>
      )}
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
        // 21 ticks: the raw TLX scale is coarse enough to answer quickly and
        // fine enough to average, and a continuous slider would invent
        // precision the instrument does not have.
        return (
          <SliderRow
            item={item}
            value={value}
            disabled={disabled}
            ticks={21}
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
    return <h3 className="form-section">{item.label}</h3>
  }

  const showLabel = item.type !== 'checkbox'

  return (
    <div
      className="field"
      style={isMissing ? { borderLeft: '3px solid var(--bad)', paddingLeft: '0.9rem' } : undefined}
    >
      {showLabel && (
        <div className="field-label">
          {item.label}
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
