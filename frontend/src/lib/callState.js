/**
 * Call states (design doc §6 of the task spec).
 *
 * These are derived from real signals only — Twilio SDK events and the
 * backend's own session status. Nothing here is set on a timer to make the UI
 * look busy.
 *
 * Who decides what:
 *   - The Twilio SDK knows only about THIS browser's leg. Its `accept` means
 *     our own line is up, NOT that the other person answered.
 *   - The backend hears Twilio's status for the phone leg (ringing, answered,
 *     busy, no answer ...), so once our line is up it decides the state.
 */
export const State = {
  IDLE: 'IDLE',
  CONNECTING: 'CONNECTING',     // token + session + device.connect() in flight
  RINGING: 'RINGING',           // our line is up; the other phone is ringing
  ANSWERED: 'ANSWERED',         // they picked up; their audio is being connected
  CONNECTED: 'CONNECTED',       // both legs are streaming
  TRANSLATING: 'TRANSLATING',   // backend sees audio frames on the stream
  RECONNECTING: 'RECONNECTING', // SDK reconnecting / device offline
  ENDED: 'ENDED',
  ERROR: 'ERROR',
}

export const LIVE_STATES = new Set([
  State.CONNECTING,
  State.RINGING,
  State.ANSWERED,
  State.CONNECTED,
  State.TRANSLATING,
  State.RECONNECTING,
])

export const isLive = (state) => LIVE_STATES.has(state)

/** Has the other person picked up yet? */
export const isAnswered = (state) =>
  state === State.ANSWERED || state === State.CONNECTED || state === State.TRANSLATING

const COPY = {
  [State.IDLE]: 'Ready',
  [State.CONNECTING]: 'Calling…',
  [State.RINGING]: 'Ringing…',
  [State.ANSWERED]: 'Answered',
  [State.CONNECTED]: 'Connected',
  [State.TRANSLATING]: 'Translating',
  [State.RECONNECTING]: 'Reconnecting',
  [State.ENDED]: 'Call ended',
  [State.ERROR]: 'Error',
}

export const label = (state) => COPY[state] ?? state

const TONE = {
  [State.IDLE]: 'idle',
  [State.CONNECTING]: 'wait',
  [State.RINGING]: 'wait',
  [State.ANSWERED]: 'live',
  [State.CONNECTED]: 'live',
  [State.TRANSLATING]: 'live',
  [State.RECONNECTING]: 'wait',
  [State.ENDED]: 'idle',
  [State.ERROR]: 'error',
}

export const tone = (state) => TONE[state] ?? 'idle'

/** The backend's view of the far side, once our own line is up. */
const FAR_SIDE = {
  CONNECTING: State.RINGING,     // dialled; Twilio has not reported ringing yet
  RINGING: State.RINGING,
  ANSWERED: State.ANSWERED,
  CONNECTED: State.CONNECTED,
  TRANSLATING: State.TRANSLATING,
}

/**
 * Merge this browser's state with the backend's.
 *
 * Before our line is up we can only be connecting (or already ringing, if the
 * backend says so). After that the backend is authoritative about the far
 * side, so its status wins — that is what makes "Connected" mean the other
 * person actually answered.
 */
export function reconcile(local, backendStatus) {
  if (!backendStatus) return local
  if (local === State.ERROR || local === State.ENDED) return local
  if (local === State.RECONNECTING) return local
  if (backendStatus === 'ENDED') return State.ENDED

  if (local === State.CONNECTING) {
    return backendStatus === 'RINGING' ? State.RINGING : local
  }
  return FAR_SIDE[backendStatus] ?? local
}

/**
 * What to tell the user when a call ends, from the backend's `end_reason`.
 * Null for an ordinary hang-up, which needs no explanation.
 *
 * Most carriers report a declined call as "busy", so that one says both.
 */
const END_MESSAGES = {
  busy: 'The line was busy or the call was declined.',
  declined: 'The call was declined.',
  'no-answer': 'No answer.',
  failed: 'The number could not be reached.',
  canceled: 'Call cancelled.',
}

export const endMessage = (reason) => END_MESSAGES[reason] ?? null

/** Short form for the other person's panel. */
const END_SHORT = {
  busy: 'Busy / declined',
  declined: 'Declined',
  'no-answer': 'No answer',
  failed: 'Unreachable',
  canceled: 'Cancelled',
}

export const endShort = (reason) => END_SHORT[reason] ?? null
