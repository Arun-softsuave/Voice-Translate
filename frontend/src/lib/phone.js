// E.164. The backend re-validates; this exists to fail fast in the UI rather
// than to be the security boundary.
const E164 = /^\+[1-9]\d{7,14}$/

export const normalise = (raw) => raw.replace(/[\s\-()]/g, '')

export const isValid = (raw) => E164.test(normalise(raw))

export function validationMessage(raw) {
  const value = normalise(raw)
  if (!value) return 'Enter the number you want to call.'
  if (!value.startsWith('+')) return 'Start with the country code, e.g. +91'
  if (!E164.test(value)) return 'That does not look like a complete number.'
  return null
}

/**
 * Group a number for display, working on partial input as well as complete.
 *
 *   +91        -> +91
 *   +918       -> +91 8
 *   +9187546   -> +91 87546
 *   +918754677067 -> +91 87546 77067
 *
 * A separator is only added once there is a digit to follow it, so pressing
 * backspace always removes something visible instead of deleting a space that
 * immediately reappears.
 *
 * Only +91 is grouped. Other country codes have their own conventions and
 * guessing them wrong is worse than leaving the digits unbroken.
 */
export function format(raw) {
  const value = String(raw ?? '')
  const digits = value.replace(/\D/g, '')

  if (!digits) return value.trimStart().startsWith('+') ? '+' : ''

  if (digits.startsWith('91')) {
    const rest = digits.slice(2)
    let out = '+91'
    if (rest.slice(0, 5)) out += ' ' + rest.slice(0, 5)
    if (rest.slice(5)) out += ' ' + rest.slice(5)
    return out
  }

  return '+' + digits
}
