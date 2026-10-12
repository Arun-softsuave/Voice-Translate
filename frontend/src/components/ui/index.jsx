/* Shared primitives. Every visual value comes from tokens.css. */

import { useEffect, useRef, useState } from 'react'

export function Button({ variant = 'ghost', active, small, children, ...rest }) {
  const classes = ['btn', `btn--${variant}`]
  if (active) classes.push('btn--on')
  if (small) classes.push('btn--small')
  return (
    <button className={classes.join(' ')} {...rest}>
      {children}
    </button>
  )
}

export function Field({ label, hint, error, children, htmlFor }) {
  return (
    <label className="field" htmlFor={htmlFor}>
      <span className="field__label">{label}</span>
      {children}
      {(error || hint) && (
        <span className={`field__hint${error ? ' field__hint--error' : ''}`}>
          {error || hint}
        </span>
      )}
    </label>
  )
}

export function Select({ invalid, children, ...rest }) {
  return (
    <select className={`control${invalid ? ' control--invalid' : ''}`} {...rest}>
      {children}
    </select>
  )
}

export function Input({ invalid, ...rest }) {
  return <input className={`control${invalid ? ' control--invalid' : ''}`} {...rest} />
}

export function Status({ tone = 'idle', children }) {
  return (
    <span className="status">
      <span className={`dot dot--${tone}`} aria-hidden="true" />
      {children}
    </span>
  )
}

/** Live microphone level from the Twilio SDK — real samples, not decoration. */
export function Meter({ level = 0, bars = 5 }) {
  return (
    <span className="meter" aria-hidden="true">
      {Array.from({ length: bars }, (_, i) => {
        const threshold = (i + 1) / bars
        const on = level >= threshold * 0.6
        return (
          <span
            key={i}
            className={`meter__bar${on ? ' meter__bar--on' : ''}`}
            style={{ height: `${5 + (on ? level * 13 : 0) + i}px` }}
          />
        )
      })}
    </span>
  )
}

export function Toast({ tone = 'error', message, onClose }) {
  if (!message) return null
  return (
    <div className={`toast toast--${tone}`} role="alert">
      <span>{message}</span>
      <button className="toast__close" onClick={onClose} aria-label="Dismiss">
        ×
      </button>
    </div>
  )
}

/** A password field with a show/hide switch, so long phrases can be checked. */
export function PasswordInput({ invalid, ...rest }) {
  const [shown, setShown] = useState(false)
  return (
    <span className="secret">
      <Input type={shown ? 'text' : 'password'} invalid={invalid} {...rest} />
      <button
        type="button"
        className="secret__toggle"
        onClick={() => setShown((s) => !s)}
        aria-label={shown ? 'Hide password' : 'Show password'}
        aria-pressed={shown}
      >
        {shown ? 'Hide' : 'Show'}
      </button>
    </span>
  )
}

/** An error or notice above a form. Focused when it appears, and announced. */
export function Alert({ tone = 'error', children }) {
  const ref = useRef(null)
  useEffect(() => {
    if (children) ref.current?.focus()
  }, [children])
  if (!children) return null
  return (
    <div ref={ref} className={`alert alert--${tone}`} role="alert" tabIndex={-1}>
      {children}
    </div>
  )
}

/** What an empty list will hold, instead of a blank space. */
export function Empty({ title, children }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {children && <p className="empty__body">{children}</p>}
    </div>
  )
}
