/**
 * Live caption state.
 *
 * The backend streams lines already filtered for this viewer and already in
 * this viewer's language (see backend/app/services/captions.py):
 *   {id, speaker: 'you' | 'them', text, lang, from, t}
 * Every event carries a line's FULL text so far, so updating is a replace by
 * id and a missed event can never leave a line garbled.
 */

/** A line counts as still being spoken this long after its last update. */
export const LIVE_MS = 1500

export const initialCaptions = []

const byId = (a, b) => a.id - b.id

/**
 * Reducer. `at` is when the event arrived (ms), used only for `isLive`.
 *   {type: 'snapshot', lines, at}  everything so far; replaces the list
 *   {type: 'caption', line, at}    one new or updated line
 *   {type: 'reset'}                a new call
 */
export function captionsReducer(lines, action) {
  switch (action.type) {
    case 'snapshot': {
      // Keep arrival times for lines we already had, so a reconnect does
      // not make every old line flash as "speaking" again.
      const known = new Map(lines.map((l) => [l.id, l.at]))
      return action.lines
        .map((l) => ({ ...l, at: known.get(l.id) ?? 0 }))
        .sort(byId)
    }
    case 'caption': {
      const line = { ...action.line, at: action.at }
      const index = lines.findIndex((l) => l.id === line.id)
      if (index === -1) return [...lines, line].sort(byId)
      if (lines[index].text === line.text) return lines
      const next = lines.slice()
      next[index] = line
      return next
    }
    case 'reset':
      return initialCaptions
    default:
      return lines
  }
}

/**
 * Still being spoken: the newest line from that speaker, updated recently.
 * Only one line per speaker can be live, so an older line never glows.
 */
export function isLive(line, lines, now) {
  if (now - line.at > LIVE_MS) return false
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].speaker === line.speaker) return lines[i].id === line.id
  }
  return false
}

/** "01:04" from seconds since the call started. */
export function stamp(seconds) {
  const s = Math.max(0, Math.floor(seconds))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
