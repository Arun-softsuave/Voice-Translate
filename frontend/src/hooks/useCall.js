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
 *
 * The SDK's `accept` only means our own line is up, so it moves the call to
 * RINGING; from then on the backend's status (which hears Twilio's ringing /
 * answered / busy events for the phone) decides.
 */
export function useCall() {
  const [state, setState] = useState(State.IDLE)
  const [sessionId, setSessionId] = useState(null)
  const [session, setSession] = useState(null)
  const [endReason, setEndReason] = useState(null)
  const [error, setError] = useState(null)
  const [muted, setMuted] = useState(false)
  const [levels, setLevels] = useState({ input: 0, output: 0 })
  const [seconds, setSeconds] = useState(0)

  const voice = useRef(null)
  const stateRef = useRef(state)
  stateRef.current = state
  const backendStatus = useRef(null)
  // Bumped by reset(), so a start() still in flight knows it was abandoned.
  const attempt = useRef(0)

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
        backendStatus.current = snapshot.status
        setSession(snapshot)
        if (snapshot.end_reason) setEndReason(snapshot.end_reason)
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

  /**
   * When the call ends, ask once why. The backend hangs up our leg itself on
   * busy / no answer, which ends polling before it could see the reason; it
   * keeps the ended call readable for a while for exactly this.
   */
  useEffect(() => {
    if (state !== State.ENDED || !sessionId || endReason) return
    let cancelled = false
    api.session(sessionId)
      .then((snapshot) => {
        if (!cancelled && snapshot?.end_reason) setEndReason(snapshot.end_reason)
      })
      .catch(() => { /* forgotten already: nothing to explain */ })
    return () => { cancelled = true }
  }, [state, sessionId, endReason])

  const start = useCallback(
    async ({ sourceLanguage, targetLanguage, phoneNumber }) => {
      setError(null)
      setSeconds(0)
      setSession(null)
      setEndReason(null)
      setMuted(false)
      backendStatus.current = null
      setState(State.CONNECTING)
      const mine = attempt.current
      const abandoned = () => attempt.current !== mine

      try {
        await VoiceConnection.requestMicrophone()

        if (abandoned()) return
        const created = await api.start({ sourceLanguage, targetLanguage, phoneNumber })
        if (abandoned()) {
          api.end(created.session_id).catch(() => {})
          return
        }
        setSessionId(created.session_id)

        const { token } = await api.token()
        if (abandoned()) {
          api.end(created.session_id).catch(() => {})
          return
        }

        const connection = new VoiceConnection({
          // Our own line is up. Whether the other person answered is the
          // backend's call, so start from "ringing" and let it move us on.
          onAccept: () => setState((s) => reconcile(
            s === State.CONNECTING ? State.RINGING : s, backendStatus.current)),
          onReconnecting: () => setState(State.RECONNECTING),
          onReconnected: () => setState(() =>
            reconcile(State.RINGING, backendStatus.current)),
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
        if (abandoned()) return
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
        const ended = await api.end(sessionId)
        if (ended?.end_reason) setEndReason(ended.end_reason)
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
    attempt.current += 1
    voice.current?.disconnect()
    voice.current = null
    backendStatus.current = null
    setState(State.IDLE)
    setSessionId(null)
    setSession(null)
    setEndReason(null)
    setError(null)
    setSeconds(0)
    setLevels({ input: 0, output: 0 })
  }, [])

  /** Leave the call screen at once: end the call if it is still on, then reset. */
  const leave = useCallback(() => {
    if (sessionId && isLive(stateRef.current)) {
      api.end(sessionId).catch(() => { /* the backend may have ended it already */ })
    }
    reset()
  }, [sessionId, reset])

  useEffect(() => () => voice.current?.disconnect(), [])

  return {
    state,
    session,
    sessionId,
    endReason,
    error,
    muted,
    levels,
    seconds,
    start,
    hangUp,
    toggleMute,
    reset,
    leave,
    dismissError: () => setError(null),
  }
}
