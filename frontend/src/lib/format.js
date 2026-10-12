/** How times, lengths and outcomes of past calls read on screen. */

const dateTime = new Intl.DateTimeFormat(undefined, {
  day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
})

/** A server time ("…Z", UTC) in the reader's own time zone. */
export function when(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : dateTime.format(d)
}

/** 0:42, 12:05, 1:02:09 */
export function duration(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

const OUTCOMES = {
  completed: 'Completed',
  user_ended: 'Completed',
  canceled: 'Cancelled',
  busy: 'Busy',
  declined: 'Declined',
  'no-answer': 'No answer',
  failed: 'Failed',
  user_disabled: 'Ended by admin',
  server_restart: 'Interrupted',
}

/** A call's outcome in words, and the status-dot tone that goes with it. */
export function outcome(call) {
  if (call.status === 'in_progress') return { label: 'In progress', tone: 'live' }
  if (call.status === 'failed') return { label: 'Not placed', tone: 'error' }
  const label = OUTCOMES[call.end_reason] ??
    (call.end_reason?.startsWith('twilio_') ? 'Not placed' : 'Ended')
  const good = call.duration_s !== null && call.duration_s !== undefined
  return { label, tone: good ? 'idle' : 'wait' }
}

/** Start of a local day, as the UTC instant the server filters by. */
export function dayStart(yyyyMmDd, plusDays = 0) {
  if (!yyyyMmDd) return undefined
  const [y, m, d] = yyyyMmDd.split('-').map(Number)
  return new Date(y, m - 1, d + plusDays).toISOString()
}
