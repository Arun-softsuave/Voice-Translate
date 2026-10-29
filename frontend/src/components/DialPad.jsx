import { useEffect, useRef } from 'react'

const KEYS = [
  ['1', ''], ['2', 'ABC'], ['3', 'DEF'],
  ['4', 'GHI'], ['5', 'JKL'], ['6', 'MNO'],
  ['7', 'PQRS'], ['8', 'TUV'], ['9', 'WXYZ'],
  ['*', ''], ['0', '+'], ['#', ''],
]

const SENDABLE = '0123456789*#'

/**
 * The in-call keypad, for answering a recorded menu ("press one for sales").
 *
 * The two legs of a translated call are never joined inside Twilio — each one
 * ends at our server — so a key pressed here cannot travel to the far end by
 * itself. It is sent to the server, which plays the tone down the other line.
 *
 * `sent` is what has been pressed so far, shown back because a keypad with no
 * feedback leaves you unsure whether anything happened.
 */
export function DialPad({ sent = '', onPress, busy = false }) {
  const panel = useRef(null)

  // Typing the number row should work too: by the time you have found the
  // menu option on screen, reaching for the mouse costs you the window.
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (!SENDABLE.includes(event.key)) return
      const field = event.target
      if (field.tagName === 'INPUT' || field.tagName === 'TEXTAREA') return
      event.preventDefault()
      onPress(event.key)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onPress])

  useEffect(() => {
    panel.current?.querySelector('button')?.focus()
  }, [])

  return (
    <div className="dialpad" ref={panel}>
      <p className="dialpad__sent" role="status" aria-live="polite">
        {sent
          ? <>Sent <b>{sent}</b></>
          : <span className="dialpad__hint">Press the key the menu asked for</span>}
      </p>
      <div className="dialpad__keys">
        {KEYS.map(([key, letters]) => (
          <button
            key={key}
            type="button"
            className="dialpad__key"
            disabled={busy}
            onClick={() => onPress(key)}
            aria-label={`Press ${key}`}
          >
            <span className="dialpad__digit">{key}</span>
            <span className="dialpad__letters" aria-hidden="true">{letters}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
