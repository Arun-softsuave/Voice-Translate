import { State, endMessage, endShort, isAnswered, label, tone } from '../lib/callState'
import { labelOf } from '../lib/languages'
import { format } from '../lib/phone'
import { Captions } from './Captions'
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
  onHangUp,
  onToggleMute,
  onReset,
}) {
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

        <div className="controls">
          {finished ? (
            <Button variant="primary" onClick={onReset}>
              New call
            </Button>
          ) : (
            <>
              <Button onClick={() => onHangUp()} variant="danger">
                End call
              </Button>
              <Button onClick={onToggleMute} active={muted}>
                {muted ? 'Unmute' : 'Mute'}
              </Button>
            </>
          )}
        </div>
      </footer>
    </section>
  )
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
