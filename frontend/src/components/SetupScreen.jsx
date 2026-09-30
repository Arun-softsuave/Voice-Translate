import { useEffect, useMemo, useState } from 'react'

import { allows, firstCode } from '../lib/languages'
import { format, normalise, validationMessage } from '../lib/phone'
import { Button, Field, Input, Select } from './ui'

/**
 * `sources` and `targets` come from the backend and are NOT the same list.
 * The translate model hears far more languages than it can speak, so a target
 * the model cannot produce must never be selectable.
 */
export function SetupScreen({ onStart, busy, demoMode, sources, targets }) {
  const [source, setSource] = useState(() => firstCode(sources, 'ta'))
  const [target, setTarget] = useState(
    () => targets.find((l) => l.code !== firstCode(sources))?.code
          ?? firstCode(targets, 'hi'),
  )
  const [phone, setPhone] = useState('+91')
  const [touched, setTouched] = useState(false)

  // If the lists change under us — switching translation backend does exactly
  // that — a previously valid choice can become unavailable. Fall back rather
  // than leave a selection the server would reject.
  useEffect(() => {
    if (!allows(sources, source)) setSource(firstCode(sources, source))
    if (!allows(targets, target)) setTarget(firstCode(targets, target))
  }, [sources, targets, source, target])

  const phoneError = useMemo(() => {
    if (demoMode) return null
    return touched ? validationMessage(phone) : null
  }, [phone, touched, demoMode])

  const sameLanguage = source === target
  const canStart =
    !busy && !sameLanguage && (demoMode || validationMessage(phone) === null)

  // Swapping is only meaningful when the reversed pair is actually offered.
  // With asymmetric lists the reverse may not exist — e.g. Telugu can be
  // spoken but not produced — so the control is disabled rather than silently
  // creating a pair the backend will reject.
  const canSwap = allows(sources, target) && allows(targets, source)

  const swap = () => {
    if (!canSwap) return
    setSource(target)
    setTarget(source)
  }

  const submit = (event) => {
    event.preventDefault()
    setTouched(true)
    if (!canStart) return
    onStart({
      sourceLanguage: source,
      targetLanguage: target,
      // Send bare E.164 — the display grouping is for the human, not the API.
      phoneNumber: demoMode ? null : normalise(phone),
    })
  }

  const option = (l) => (
    <option key={l.code} value={l.code}>
      {l.label} — {l.native}
    </option>
  )

  // The <form> stays the outermost element and still contains every control,
  // so Enter-to-submit is structurally untouched by the split layout.
  return (
    <form className="setup" onSubmit={submit}>
      <div className="setup__brand enter">
        <p className="eyebrow">Real-Time Interpretation</p>
        <h1 className="display">
          Speak freely.<br />
          We <em>translate</em> as you talk.
        </h1>
        <p className="lede">
          Your words reach them in their language, and theirs reach you in yours —
          live, on an ordinary phone call.
        </p>
        <p className="setup__mark">Interpreter · R&amp;D preview</p>
      </div>

      <div className="setup__form enter">
        <div className="setup__inner">
          <div className="pair">
            <Field label="Your language" htmlFor="source">
              <Select
                id="source"
                value={source}
                disabled={busy}
                onChange={(e) => setSource(e.target.value)}
              >
                {sources.map(option)}
              </Select>
            </Field>

            <button
              type="button"
              className="pair__swap"
              onClick={swap}
              disabled={busy || !canSwap}
              aria-label="Swap languages"
              title={canSwap
                ? 'Swap languages'
                : 'This pair cannot be reversed on the current model'}
            >
              ⇄
            </button>

            <Field
              label="They hear"
              htmlFor="target"
              error={sameLanguage ? 'Choose two different languages.' : null}
            >
              <Select
                id="target"
                value={target}
                disabled={busy}
                invalid={sameLanguage}
                onChange={(e) => setTarget(e.target.value)}
              >
                {targets.map(option)}
              </Select>
            </Field>
          </div>

          <hr className="rule" />

          {demoMode ? (
            <Field
              label="Mobile number"
              hint="Demo mode is on — no real call is placed and nothing is charged."
            >
              <Input value="Demo mode — browser to browser" disabled readOnly />
            </Field>
          ) : (
            <Field
              label="Mobile number"
              htmlFor="phone"
              error={phoneError}
              hint="Include the country code, e.g. +91 87546 77067"
            >
              <Input
                id="phone"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                placeholder="+91 XXXXX XXXXX"
                value={phone}
                disabled={busy}
                invalid={Boolean(phoneError)}
                onBlur={() => setTouched(true)}
                onChange={(e) => setPhone(format(e.target.value))}
              />
            </Field>
          )}

          <div style={{ marginTop: 'var(--s5)' }}>
            <Button type="submit" variant="primary" disabled={!canStart}>
              {busy ? 'Connecting…' : 'Start translated call'}
            </Button>
          </div>

          <p className="footnote">
            Your microphone is used only for the duration of the call.
          </p>
        </div>
      </div>
    </form>
  )
}
