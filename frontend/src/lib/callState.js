/**
 * Call states (design doc §6 of the task spec).
 *
 * These are derived from real signals only — Twilio SDK events and the
 * backend's own session status. Nothing here is set on a timer to make the UI
 * look busy.
 */
export const State = {
  IDLE: 'IDLE',
  CONNECTING: 'CONNECTING',     // token + session + device.connect() in flight
  RINGING: 'RINGING',           // Twilio told the backend the far leg is ringing
  CONNECTED: 'CONNECTED',       // SDK 'accept' — media path is open
  TRANSLATING: 'TRANSLATING',   // backend sees audio frames on the stream
  RECONNECTING: 'RECONNECTING', // SDK reconnecting / device offline
  ENDED: 'ENDED',
  ERROR: 'ERROR',
}

export const LIVE_STATES = new Set([
  State.CONNECTING,
  State.RINGING,
  State.CONNECTED,
  State.TRANSLATING,
  State.RECONNECTING,
])

export const isLive = (state) => LIVE_STATES.has(state)

const COPY = {
  [State.IDLE]: 'Ready',
  [State.CONNECTING]: 'Connecting',
  [State.RINGING]: 'Calling',
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
  [State.CONNECTED]: 'live',
  [State.TRANSLATING]: 'live',
  [State.RECONNECTING]: 'wait',
  [State.ENDED]: 'idle',
  [State.ERROR]: 'error',
}

export const tone = (state) => TONE[state] ?? 'idle'

/**
 * The backend is authoritative about the far end of the call (it is the only
 * party that sees Twilio's status callbacks and the media stream). The SDK is
 * authoritative about this browser's own leg. This merges the two without
 * letting a slow poll drag a live call backwards.
 */
export function reconcile(local, backendStatus) {
  if (!backendStatus) return local
  if (local === State.ERROR || local === State.ENDED) return local
  if (local === State.RECONNECTING) return local

  if (backendStatus === 'TRANSLATING' && local === State.CONNECTED) {
    return State.TRANSLATING
  }
  if (backendStatus === 'RINGING' && local === State.CONNECTING) {
    return State.RINGING
  }
  if (backendStatus === 'ENDED') return State.ENDED
  return local
}
