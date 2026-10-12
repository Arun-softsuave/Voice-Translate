import { useEffect, useMemo, useRef, useState } from 'react'

import { AdminPage } from './components/AdminPage'
import { LoginScreen, RequestAccessScreen } from './components/AuthScreens'
import { CallScreen } from './components/CallScreen'
import { HistoryPage } from './components/HistoryPage'
import { SetupScreen } from './components/SetupScreen'
import { TopBar } from './components/TopBar'
import { Toast } from './components/ui'
import { useCall } from './hooks/useCall'
import { useCaptions } from './hooks/useCaptions'
import { api, whenSignedOut } from './lib/api'
import { home, redirectFor, safeNext } from './lib/auth'
import { State, isLive } from './lib/callState'
import { navigate, useRoute } from './lib/router'

export default function App() {
  const route = useRoute()
  // undefined = not asked yet; null = signed out.
  const [user, setUser] = useState(undefined)
  const call = useCall()
  const captions = useCaptions(call.sessionId, isLive(call.state))
  const [demoMode, setDemoMode] = useState(null)
  // The backend decides which languages are offered, and the two directions
  // differ on the translate model. null = not known yet.
  const [languages, setLanguages] = useState(null)
  const [notice, setNotice] = useState(null)
  const request = useRef({ source: null, target: null, phone: null })

  // Who is signed in. Any request the server refuses for want of a sign-in
  // (expired, signed out elsewhere, access turned off) signs this page out.
  useEffect(() => {
    whenSignedOut(() => setUser(null))
    api.me().then(setUser).catch(() => setUser(null))
  }, [])

  // Signed out in the middle of a call (say, access turned off by an admin):
  // the server has already ended it; let go of the line here too.
  const latestCall = useRef(call)
  latestCall.current = call
  useEffect(() => {
    if (user === null && isLive(latestCall.current.state)) latestCall.current.leave()
  }, [user])

  // Send the browser where this person may be: /login when signed out, away
  // from /login when signed in, away from /admin for members.
  const redirect = user === undefined ? null : redirectFor(route, user)
  useEffect(() => {
    if (redirect) navigate(redirect, { replace: true })
  }, [redirect])

  const signedIn = (who) => {
    setUser(who)
    navigate(safeNext(route.query.next, who) ?? home(who), { replace: true })
  }

  const signOut = async () => {
    try {
      await api.logout()
    } finally {
      setUser(null)
      navigate('/login', { replace: true })
    }
  }

  // The backend decides whether this is a demo run; the UI must not guess.
  useEffect(() => {
    api
      .health()
      .then((h) => {
        setDemoMode(Boolean(h.demo_mode))
        setLanguages(h.languages ?? { source: [], target: [] })
      })
      .catch(() => {
        setDemoMode(false)
        setLanguages({ source: [], target: [] })
        setNotice('Cannot reach the backend. Start it on port 8000 and reload.')
      })
  }, [])

  const handleStart = (form) => {
    request.current = {
      source: form.sourceLanguage,
      target: form.targetLanguage,
      phone: form.phoneNumber,
    }
    call.start(form)
  }

  const onSetupScreen = call.state === State.IDLE

  // The app is one page that swaps screens, so without this the browser's
  // Back button leaves the app altogether (often to an empty new tab). Entering
  // the call screen adds a history entry; Back then returns to the setup
  // screen, hanging up first if the call is still on.
  const onCallEntry = useRef(false)

  useEffect(() => {
    if (!onSetupScreen && !onCallEntry.current) {
      window.history.pushState({ screen: 'call' }, '')
      onCallEntry.current = true
    } else if (onSetupScreen && onCallEntry.current) {
      // Left by "New call" rather than Back: drop the entry we added, so Back
      // from the setup screen does not first land on a dead call screen.
      onCallEntry.current = false
      window.history.back()
    }
  }, [onSetupScreen])

  useEffect(() => {
    const onBack = () => {
      if (!onCallEntry.current) return
      onCallEntry.current = false
      latestCall.current.leave()
    }
    window.addEventListener('popstate', onBack)
    return () => window.removeEventListener('popstate', onBack)
  }, [])

  // Every language either direction offers, for naming them in the history.
  const catalogue = useMemo(() => {
    if (!languages) return []
    const seen = new Map(languages.source.map((l) => [l.code, l]))
    for (const l of languages.target) if (!seen.has(l.code)) seen.set(l.code, l)
    return [...seen.values()]
  }, [languages])

  // Until /health and /me answer we know neither who this is, whether a phone
  // number is required, nor which languages are offered, so we hold the
  // screen rather than show the wrong one and swap it.
  if (demoMode === null || languages === null || user === undefined || redirect) {
    return (
      <main className="shell">
        <div className="booting">
          <p className="eyebrow">Real-Time Interpretation</p>
          <p className="lede">Preparing…</p>
        </div>
      </main>
    )
  }

  if (!user) {
    return (
      <main className="shell">
        {route.name === 'request'
          ? <RequestAccessScreen />
          : <LoginScreen onSignedIn={signedIn} />}
        <Toast tone="info" message={notice} onClose={() => setNotice(null)} />
      </main>
    )
  }

  if (route.name !== 'call') {
    return (
      <main className="shell shell--app">
        <TopBar user={user} route={route} onSignOut={signOut} />
        {route.name.startsWith('admin')
          ? <AdminPage route={route} user={user} catalogue={catalogue} />
          : <HistoryPage route={route} catalogue={catalogue} />}
      </main>
    )
  }

  return (
    <main className={`shell${onSetupScreen ? ' shell--app' : ''}`}>
      {onSetupScreen && <TopBar user={user} route={route} onSignOut={signOut} />}
      {onSetupScreen ? (
        <SetupScreen
          onStart={handleStart}
          busy={false}
          demoMode={demoMode}
          sources={languages.source}
          targets={languages.target}
        />
      ) : (
        <CallScreen
          state={call.state}
          session={call.session}
          seconds={call.seconds}
          levels={call.levels}
          muted={call.muted}
          source={request.current.source}
          target={request.current.target}
          catalogue={languages.source}
          phoneNumber={request.current.phone}
          demoMode={demoMode}
          captions={captions.lines}
          endReason={call.endReason}
          now={captions.now}
          onHangUp={call.hangUp}
          onToggleMute={call.toggleMute}
          onReset={call.reset}
        />
      )}

      <Toast tone="error" message={call.error} onClose={call.dismissError} />
      {!call.error && (
        <Toast tone="info" message={notice} onClose={() => setNotice(null)} />
      )}
    </main>
  )
}
