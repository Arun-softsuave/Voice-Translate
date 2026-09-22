import { useEffect, useRef, useState } from 'react'

import { CallScreen } from './components/CallScreen'
import { SetupScreen } from './components/SetupScreen'
import { Toast } from './components/ui'
import { useCall } from './hooks/useCall'
import { api } from './lib/api'
import { State } from './lib/callState'

export default function App() {
  const call = useCall()
  const [demoMode, setDemoMode] = useState(null)
  const [notice, setNotice] = useState(null)
  const request = useRef({ source: 'ta', target: 'hi', phone: null })

  // The backend decides whether this is a demo run; the UI must not guess.
  useEffect(() => {
    api
      .health()
      .then((h) => setDemoMode(Boolean(h.demo_mode)))
      .catch(() => {
        setDemoMode(false)
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

  // Until /health answers we do not know whether a phone number is required,
  // so we hold the screen rather than show the wrong form and swap it.
  if (demoMode === null) {
    return (
      <main className="shell">
        <div className="booting">
          <p className="eyebrow">Real-Time Interpretation</p>
          <p className="lede">Preparing…</p>
        </div>
      </main>
    )
  }

  return (
    <main className="shell">
      {onSetupScreen ? (
        <SetupScreen onStart={handleStart} busy={false} demoMode={demoMode} />
      ) : (
        <CallScreen
          state={call.state}
          session={call.session}
          seconds={call.seconds}
          levels={call.levels}
          muted={call.muted}
          source={request.current.source}
          target={request.current.target}
          phoneNumber={request.current.phone}
          demoMode={demoMode}
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
