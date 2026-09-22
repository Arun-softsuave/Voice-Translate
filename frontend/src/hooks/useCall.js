import { useCallback, useEffect, useRef, useState } from 'react'

import { api, ApiError } from '../lib/api'
import { State, isLive, reconcile } from '../lib/callState'
import { VoiceConnection } from '../lib/twilioDevice'

const POLL_MS = 1500

/**
 * Owns the whole call lifecycle.
 *
 * State comes from two real sources and nowhere else:
 *   - the Twilio SDK, for this browser's own leg
 *   - the backend session, for the far leg and the media stream
 */
export function useCall() {
  const [state, setState] = useState(State.IDLE)
  const [sessionId, setSessionId] = useState(null)
  const [session, setSession] = useState(null)
  const [error, setError] = useState(null)
  const [muted, setMuted] = useState(false)
  const [levels, setLevels] = useState({ input: 0, output: 0 })
  const [seconds, setSeconds] = useState(0)

  const voice = useRef(null)
  const stateRef = useRef(state)
  stateRef.current = state

  const fail = useCallback((message) => {
    setError(message)
    setState(State.ERROR)
    voice.current?.disconnect()
    voice.current = null
  }, [])

  /** Duration ticks only while the media path is actually open. */
  useEffect(() => {
    if (state !== State.CONNECTED && state !== State.TRANSLATING) return
    const id = setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [state])

  /** Poll the backend for the authoritative session view. */
  useEffect(() => {
    if (!sessionId || !isLive(stateRef.current)) return

    let cancelled = false
    const tick = async () => {
      try {
        const snapshot = await api.session(sessionId)
        if (cancelled) return
        setSession(snapshot)
        setState((local) => reconcile(local, snapshot.status))
      } catch (err) {
        // A 404 means the backend already tore the session down.
        if (!cancelled && err instanceof ApiError && err.status === 404) {
          setSession(null)
        }
      }
    }

    tick()
    const id = setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [sessionId, state])

  const start = useCallback(
    async ({ sourceLanguage, targetLanguage, phoneNumber }) => {
      setError(null)
      setSeconds(0)
      setSession(null)
      setMuted(false)
      setState(State.CONNECTING)

      try {
        await VoiceConnection.requestMicrophone()

        const created = await api.start({ sourceLanguage, targetLanguage, phoneNumber })
        setSessionId(created.session_id)

        const { token } = await api.token()

        const connection = new VoiceConnection({
          onAccept: () => setState(State.CONNECTED),
          onReconnecting: () => setState(State.RECONNECTING),
          onReconnected: () => setState(State.CONNECTED),
          onVolume: (input, output) => setLevels({ input, output }),
          onDisconnect: (reason) => {
            setLevels({ input: 0, output: 0 })
            setState((current) =>
              current === State.ERROR ? current : State.ENDED,
            )
            if (reason === 'rejected') setError('The call was rejected.')
          },
          onError: (message) => fail(message),
        })

        voice.current = connection
        // `session_id` reaches the TwiML webhook as a POST parameter.
        await connection.connect(token, { session_id: created.session_id })
      } catch (err) {
        fail(err instanceof ApiError ? err.message : err.message || 'Could not start the call.')
      }
    },
    [fail],
  )

  const hangUp = useCallback(async () => {
    voice.current?.disconnect()
    voice.current = null
    setLevels({ input: 0, output: 0 })
    setState(State.ENDED)
    if (sessionId) {
      try {
        await api.end(sessionId)
      } catch {
        /* the backend may have ended it already */
      }
    }
  }, [sessionId])

  const toggleMute = useCallback(() => {
    if (!voice.current) return
    const next = !voice.current.isMuted()
    voice.current.mute(next)
    setMuted(next)
  }, [])

  const reset = useCallback(() => {
    voice.current?.disconnect()
    voice.current = null
    setState(State.IDLE)
    setSessionId(null)
    setSession(null)
    setError(null)
    setSeconds(0)
    setLevels({ input: 0, output: 0 })
  }, [])

  useEffect(() => () => voice.current?.disconnect(), [])

  return {
    state,
    session,
    sessionId,
    error,
    muted,
    levels,
    seconds,
    start,
    hangUp,
    toggleMute,
    reset,
    dismissError: () => setError(null),
  }
}
