import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { State } from '../lib/callState'
import { CallScreen } from './CallScreen'

const catalogue = [
  { code: 'en', label: 'English', native: 'English' },
  { code: 'es', label: 'Spanish', native: 'Español' },
  { code: 'ta', label: 'Tamil', native: 'தமிழ்' },
  { code: 'hi', label: 'Hindi', native: 'हिन्दी' },
]

const render = (props) => renderToStaticMarkup(
  <CallScreen
    state={State.TRANSLATING}
    session={{ participants: { A: { connected: true }, B: { connected: true } } }}
    seconds={102}
    levels={{ input: 0, output: 0 }}
    muted={false}
    source="en"
    target="es"
    phoneNumber="+919876543210"
    demoMode={false}
    catalogue={catalogue}
    onHangUp={() => {}}
    onToggleMute={() => {}}
    onReset={() => {}}
    {...props}
  />,
)

describe('CallScreen captions', () => {
  it('shows your words and the translation you hear, both in your language', () => {
    const html = render({
      now: 10_000,
      captions: [
        { id: 1, speaker: 'you', text: 'Can you come at ten?', lang: 'en', from: null, t: 64, at: 0 },
        { id: 2, speaker: 'them', text: 'Yes, at ten.', lang: 'en', from: 'es', t: 69, at: 0 },
      ],
    })

    expect(html).toContain('call--captions')
    expect(html).toContain('You said')
    expect(html).toContain('Can you come at ten?')
    expect(html).toContain('They said · from Spanish')
    expect(html).toContain('Yes, at ten.')
    expect(html).toContain('in English')
    expect(html).toContain('01:04')
    expect(html).not.toContain('cap--live')
  })

  it('marks the line still being spoken', () => {
    const html = render({
      now: 10_000,
      captions: [{ id: 1, speaker: 'them', text: 'Shall I', lang: 'en', from: 'es', t: 98, at: 9_800 }],
    })
    expect(html).toContain('cap--live')
    expect(html).toContain('Speaking')
    expect(html).toContain('cap__caret')
  })

  it('a Tamil speaker gets Tamil captions and the Tamil text is tagged as Tamil', () => {
    const html = render({
      source: 'ta',
      target: 'hi',
      now: 10_000,          // long after the line arrived, so it has settled
      captions: [{ id: 1, speaker: 'them', text: 'ஆமாம், காலை 10 மணிக்கு', lang: 'ta', from: 'hi', t: 5, at: 0 }],
    })
    expect(html).toContain('in Tamil')
    expect(html).toContain('lang="ta"')
    expect(html).toContain('from Hindi')
  })

  it('keeps the original layout before the call is translating', () => {
    const html = render({ state: State.RINGING, captions: [] })
    expect(html).not.toContain('call--captions')
    expect(html).not.toContain('Live captions')
  })

  it('shows an empty state once translating but before anyone speaks', () => {
    const html = render({ captions: [] })
    expect(html).toContain('will appear here as text')
  })
})

describe('CallScreen far side', () => {
  it('says Ringing while the phone rings, not Connected', () => {
    const html = render({ state: State.RINGING, captions: [],
      session: { participants: { A: { connected: true }, B: { connected: false } } } })
    expect(html).toContain('Ringing…')
    expect(html).toContain('Waiting for answer')
    expect(html).not.toContain('>Connected<')
  })

  it('says Answered once they pick up, before their audio arrives', () => {
    const html = render({ state: State.ANSWERED, captions: [],
      session: { participants: { A: { connected: true }, B: { connected: false } } } })
    expect(html).toContain('Answered')
    expect(html).toContain('Connecting audio')
  })

  it('explains why a call did not connect', () => {
    const html = render({ state: State.ENDED, captions: [], endReason: 'no-answer',
      session: { participants: { A: { connected: false }, B: { connected: false } } } })
    expect(html).toContain('No answer.')
    expect(html).toContain('call__note')
  })

  it('shows busy-or-declined for a declined call reported as busy', () => {
    const html = render({ state: State.ENDED, captions: [], endReason: 'busy',
      session: { participants: { A: { connected: false }, B: { connected: false } } } })
    expect(html).toContain('Busy / declined')
  })

  it('an ordinary hang-up shows no extra note', () => {
    const html = render({ state: State.ENDED, captions: [], endReason: 'completed',
      session: { participants: { A: { connected: false }, B: { connected: false } } } })
    expect(html).not.toContain('call__note')
  })
})

describe('CallScreen keypad', () => {
  it('offers a keypad once the far end can hear it', () => {
    expect(render({})).toContain('Keypad')
  })

  it('offers no keypad while the phone is still ringing', () => {
    const html = render({
      state: State.RINGING,
      session: { participants: { A: { connected: true }, B: { connected: false } } },
    })
    expect(html).not.toContain('Keypad')
  })

  it('offers no keypad once the call is over', () => {
    const html = render({
      state: State.ENDED,
      endReason: 'completed',
      session: { participants: { A: { connected: false }, B: { connected: false } } },
    })
    expect(html).not.toContain('Keypad')
  })

  it('keeps the keys out of the way until they are asked for', () => {
    expect(render({})).not.toContain('dialpad__keys')
  })

  it('says plainly when the other side is being read rather than heard', () => {
    const html = render({
      session: {
        ivr_mode: true,
        participants: { A: { connected: true }, B: { connected: true } },
      },
    })
    expect(html).toContain('You are reading the other side, not hearing it.')
  })

  it('says nothing about reading on an ordinary call', () => {
    expect(render({})).not.toContain('not hearing it')
  })
})

describe('Captions of keys pressed', () => {
  const pressed = { id: 4, speaker: 'you', kind: 'pressed', text: '12', t: 9 }

  it('shows the keys sent to a menu, as keys rather than speech', () => {
    const html = render({ captions: [pressed] })
    expect(html).toContain('You pressed')
    expect(html).toContain('cap--pressed')
    expect(html).toContain('12')
  })

  it('does not dress a keypress up as something that was said', () => {
    const html = render({ captions: [pressed] })
    expect(html).not.toContain('You said')
    expect(html).not.toContain('They said')
  })
})

describe('CallScreen voicemail', () => {
  const answered = (extra) => render({
    session: {
      participants: { A: { connected: true }, B: { connected: true } },
      ...extra,
    },
  })

  it('tells the caller when voicemail is ready for their message', () => {
    expect(answered({ answered_by: 'machine_end_beep' }))
      .toContain('Voicemail — speak your message now.')
  })

  it('says a recording answered when the greeting is still playing', () => {
    expect(answered({ answered_by: 'machine_start' })).toContain('A recording answered.')
  })

  it('says nothing when a person answered', () => {
    expect(answered({ answered_by: 'human' })).not.toContain('Voicemail')
  })

  it('says nothing when the detector could not tell', () => {
    const html = answered({ answered_by: 'unknown' })
    expect(html).not.toContain('Voicemail')
    expect(html).not.toContain('recording')
  })

  it('warns that hanging up now would cut off the translation', () => {
    const html = answered({ far_backlog_s: 4.2 })
    expect(html).toContain('hanging up now would cut it off')
    expect(html).toContain('End call anyway')
  })

  it('does not nag when nothing is left to play', () => {
    const html = answered({ far_backlog_s: 0 })
    expect(html).not.toContain('cut it off')
    expect(html).toContain('End call<')
  })
})
