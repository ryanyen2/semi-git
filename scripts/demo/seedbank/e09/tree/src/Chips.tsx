export function Chips({
  traits,
  active,
  onToggle,
}: {
  traits: string[]
  active: string[]
  onToggle: (trait: string) => void
}) {
  return (
    <ul className="chips">
      {traits.map((trait) => (
        <li key={trait}>
          <button
            className={active.includes(trait) ? 'chip is-on' : 'chip'}
            aria-pressed={active.includes(trait)}
            onClick={(e) => {
              e.stopPropagation()
              onToggle(trait)
            }}
          >
            {trait}
          </button>
        </li>
      ))}
    </ul>
  )
}
