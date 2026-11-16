export function Chips({ traits }: { traits: string[] }) {
  return (
    <ul className="chips">
      {traits.map((trait) => (
        <li key={trait} className="chip">
          {trait}
        </li>
      ))}
    </ul>
  )
}
