import { useEffect, useReducer, useState } from 'react'

import { api } from '../lib/api'
import { captionsReducer, initialCaptions, LIVE_MS } from '../lib/captions'

/**
 * Live captions for this browser's side of the call.
 *
 * Streams while the call is live, keeps the lines after hang-up so they can
 * still be read, and clears them when a new call starts (sessionId changes).
 * The server also saves them to the caller's call history.
 */
export function useCaptions(sessionId, active) {
  const [lines, dispatch] = useReducer(captionsReducer, initialCaptions)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    dispatch({ type: 'reset' })
  }, [sessionId])

  useEffect(() => {
    if (!sessionId || !active) return undefined

    // withCredentials: the stream is only for the signed-in caller.
    const source = new EventSource(api.captionsUrl(sessionId), { withCredentials: true })
    const read = (handler) => (event) => {
      try {
        handler(JSON.parse(event.data))
      } catch {
        /* a malformed event is skipped; the next one carries the full line */
      }
    }

    source.addEventListener('snapshot', read((data) =>
      dispatch({ type: 'snapshot', lines: data, at: Date.now() })))
    source.addEventListener('caption', read((data) =>
      dispatch({ type: 'caption', line: data, at: Date.now() })))
    // The call is over: stop, rather than let EventSource reconnect forever.
    source.addEventListener('end', () => source.close())

    return () => source.close()
  }, [sessionId, active])

  // Re-render a little after the last update so the "speaking" glow clears.
  const latest = lines.reduce((m, l) => Math.max(m, l.at), 0)
  useEffect(() => {
    setNow(Date.now())
    if (!latest) return undefined
    const id = setTimeout(() => setNow(Date.now()), LIVE_MS + 50)
    return () => clearTimeout(id)
  }, [latest])

  return { lines, now }
}
