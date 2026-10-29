import { useEffect, useState } from 'react'

import { State, endMessage, endShort, isAnswered, label, tone } from '../lib/callState'
import { labelOf } from '../lib/languages'
import { format } from '../lib/phone'
import { Captions } from './Captions'
import { DialPad } from './DialPad'
import { Button, Meter, Status } from './ui'

const clock = (total) => {
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export function CallScreen({
  state,
  session,
  seconds,
  levels,
  muted,
  source,
  target,
  phoneNumber,
  demoMode,
  catalogue = [],
  captions = [],
  now = 0,
  endReason = null,
  digits = '',
  onHangUp,
  onToggleMute,
  onReset,
  onSendDigit,
  onSetIvrMode,
}) {
  const [padOpen, setPadOpen] = useState(false)
  const finished = state === State.ENDED || state === State.ERROR
  const translating = state === State.TRANSLATING
  const answered = isAnswered(state)
  const endNote = finished ? endMessage(endReason) : null
  const speaking = levels.input > 0.06
  const listening = levels.output > 0.06

  // Once there is anything to read, the party panes fold into a strip and
  // the captions take the stage.
  const captioned = translating || captions.length > 0

  const a = session?.participants?.A
  const b = session?.participants?.B
  const ivrMode = Boolean(session?.ivr_mode)
  const machine = machineNote(session?.answered_by)
  // Translation runs a few seconds behind the speaker, so hanging up the
  // moment you stop talking cuts off the end of what the other side hears.
  const stillPlaying = !finished && (session?.far_backlog_s ?? 0) > 1

  // The keypad is only meaningful once the far end can hear it, and it has no
  // business lingering on screen after the call.
  useEffect(() => {
    if (!answered || finished) setPadOpen(false)
  }, [answered, finished])

  return (
    <section className={`call${captioned ? ' call--captions' : ''}`}>
      <header className="call__bar">
        <Status tone={tone(state)}>{label(state)}</Status>
        <span className="call__timer">{clock(seconds)}</span>
      </header>

      <div className="call__stage">
        <div className="parties">
          <div className={`party${speaking ? ' party--active' : ''}`}>
            <span className="party__badge">1</span>
            <span>
              <span className="party__who">You</span>
              <span className="party__lang">{labelOf(source, catalogue)}</span>
            </span>
            <span className="party__meta">
              {muted ? (
                <Status tone="error">Muted</Status>
              ) : (
                <Meter level={levels.input} />
              )}
            </span>
          </div>

          <div className={`bridge${translating ? ' bridge--active' : ''}`}>
            <span className="bridge__line" />
            <span className="bridge__label">
              {translating ? 'Translating' : answered ? 'Connecting audio' : finished ? 'Ended' : 'Waiting for answer'}
            </span>
            <span className="bridge__line" />
          </div>

          <div className={`party${listening ? ' party--active' : ''}`}>
            <span className="party__badge">2</span>
            <span>
              <span className="party__who">
                {demoMode ? 'Second browser' : format(phoneNumber ?? '')}
              </span>
              <span className="party__lang">{labelOf(target, catalogue)}</span>
            </span>
            <span className="party__meta">
              {farSide({ state, finished, answered, connected: b?.connected,
                         endReason, level: levels.output })}
            </span>
          </div>
        </div>

        {captioned && (
          <Captions lines={captions} now={now} language={source} catalogue={catalogue} />
        )}
      </div>

      <footer className="call__foot">
        {endNote && (
          <p className="call__note" role="status">{endNote}</p>
        )}
        {machine && !finished && (
          <p className="call__note call__note--mode" role="status">{machine}</p>
        )}
        {ivrMode && !finished && (
          <p className="call__note call__note--mode" role="status">
            You are reading the other side, not hearing it.
          </p>
        )}
        {session && (
          <div className="diag">
            <span className="diag__item">
              <span className="diag__k">Direction</span>
              <span className="diag__v">
                {labelOf(source, catalogue)} → {labelOf(target, catalogue)}
              </span>
            </span>
            <span className="diag__item">
              <span className="diag__k">Your leg</span>
              <span className="diag__v">{a?.connected ? 'streaming' : 'offline'}</span>
            </span>
            <span className="diag__item">
              <span className="diag__k">Their leg</span>
              <span className="diag__v">{b?.connected ? 'streaming' : 'offline'}</span>
            </span>
            <span className="diag__item">
              <span className="diag__k">Sent</span>
              <span className="diag__v">{a?.frames_in ?? 0}</span>
            </span>
            <span className="diag__item">
              <span className="diag__k">Delivered</span>
              <span className="diag__v">{b?.frames_out ?? 0}</span>
            </span>
            {Boolean(a?.dropped_no_peer || b?.dropped_no_peer) && (
              <span className="diag__item">
                <span className="diag__k">Dropped — no peer</span>
                <span className="diag__v">
                  {(a?.dropped_no_peer ?? 0) + (b?.dropped_no_peer ?? 0)}
                </span>
              </span>
            )}
          </div>
        )}

        {padOpen && !finished && (
          <div className="pad">
            <DialPad sent={digits} onPress={onSendDigit} />
            <div className="pad__mode">
              <Button small active={ivrMode} onClick={() => onSetIvrMode(!ivrMode)}>
                {ivrMode ? 'Reading the menu' : 'Read the menu'}
              </Button>
              <span className="pad__note">
                {ivrMode
                  ? 'The menu is not read aloud. Follow it in the captions above.'
                  : 'A long menu is spoken back slowly. Read it instead to keep up.'}
              </span>
            </div>
          </div>
        )}

        {stillPlaying && (
          <p className="call__note call__note--wait" role="status">
            Still being spoken to the other side — hanging up now would cut it off.
          </p>
        )}

        <div className="controls">
          {finished ? (
            <Button variant="primary" onClick={onReset}>
              New call
            </Button>
          ) : (
            <>
              <Button onClick={() => onHangUp()} variant="danger">
                {stillPlaying ? 'End call anyway' : 'End call'}
              </Button>
              <Button onClick={onToggleMute} active={muted}>
                {muted ? 'Unmute' : 'Mute'}
              </Button>
              {answered && (
                <Button
                  onClick={() => setPadOpen((open) => !open)}
                  active={padOpen}
                  aria-expanded={padOpen}
                >
                  Keypad
                </Button>
              )}
            </>
          )}
        </div>
      </footer>
    </section>
  )
}

/**
 * What to say when Twilio tells us a machine answered.
 *
 * Only the cases we can act on say anything. An unknown verdict is worse than
 * silence: it would have the caller doubting a call that is perfectly fine.
 */
function machineNote(answeredBy) {
  if (answeredBy === 'machine_end_beep') return 'Voicemail — speak your message now.'
  if (answeredBy === 'machine_end_silence' || answeredBy === 'machine_end_other') {
    return 'Voicemail — the greeting has finished.'
  }
  if (answeredBy === 'machine_start') return 'A recording answered.'
  if (answeredBy === 'fax') return 'A fax machine answered.'
  return null
}

/** What the other person's panel says: the truth from Twilio, not a guess. */
function farSide({ state, finished, answered, connected, endReason, level }) {
  if (finished) {
    return <Status tone="idle">{endShort(endReason) ?? 'Disconnected'}</Status>
  }
  if (connected) return <Meter level={level} />
  if (answered) return <Status tone="live">Answered</Status>
  return <Status tone="wait">{state === State.CONNECTING ? 'Calling…' : 'Ringing…'}</Status>
}
