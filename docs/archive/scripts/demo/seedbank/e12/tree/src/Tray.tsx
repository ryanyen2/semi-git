import { useSyncExternalStore } from 'react'

// One module-level set, so a card and the header can both read the tray without
// threading state through App. Cards are rendered in a grid of 24; prop-drilling
// a tray through Grid and Card would touch three components to add one button.
const tray = new Set<string>()
const listeners = new Set<() => void>()

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

// The size doubles as the version stamp: every toggle changes it, so every
// subscriber re-reads. A tray is small enough that this is honest, not a trick.
function snapshot() {
  return tray.size
}

export function toggleTray(id: string) {
  if (tray.has(id)) tray.delete(id)
  else tray.add(id)
  listeners.forEach((listener) => listener())
}

export function TrayButton({ id }: { id: string }) {
  useSyncExternalStore(subscribe, snapshot)
  const held = tray.has(id)
  return (
    <button
      type="button"
      className={held ? 'tray-btn is-held' : 'tray-btn'}
      aria-pressed={held}
      aria-label={held ? 'take out of the tray' : 'set aside in the tray'}
      onClick={(event) => {
        // The whole card is a button that opens the detail panel.
        event.stopPropagation()
        toggleTray(id)
      }}
    >
      {held ? '★' : '☆'}
    </button>
  )
}

export function TrayCount() {
  const held = useSyncExternalStore(subscribe, snapshot)
  return (
    <span className="tray-count" aria-live="polite">
      {held === 0 ? 'tray empty' : held === 1 ? '1 in tray' : `${held} in tray`}
    </span>
  )
}
