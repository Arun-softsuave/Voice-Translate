import { describe, expect, it } from 'vitest'

import { State, endMessage, endShort, isAnswered, label, reconcile } from './callState'

describe('reconcile: our line is up, the backend decides the far side', () => {
  const up = State.RINGING   // what onAccept produces

  it.each([
    ['CONNECTING', State.RINGING],      // dialled, Twilio not ringing yet
    ['RINGING', State.RINGING],
    ['ANSWERED', State.ANSWERED],
    ['CONNECTED', State.CONNECTED],
    ['TRANSLATING', State.TRANSLATING],
    ['ENDED', State.ENDED],
  ])('backend %s -> %s', (backend, expected) => {
    expect(reconcile(up, backend)).toBe(expected)
  })

  it('never claims "Connected" just because our own leg connected', () => {
    // The old bug: SDK accept -> CONNECTED while the phone was still ringing.
    expect(reconcile(State.RINGING, 'RINGING')).not.toBe(State.CONNECTED)
  })

  it('moves forward as the backend does', () => {
    let s = State.RINGING
    for (const b of ['RINGING', 'ANSWERED', 'CONNECTED', 'TRANSLATING']) s = reconcile(s, b)
    expect(s).toBe(State.TRANSLATING)
  })
})

describe('reconcile: before our line is up', () => {
  it('stays connecting until the backend reports ringing', () => {
    expect(reconcile(State.CONNECTING, 'CONNECTING')).toBe(State.CONNECTING)
    expect(reconcile(State.CONNECTING, 'RINGING')).toBe(State.RINGING)
    expect(reconcile(State.CONNECTING, 'ANSWERED')).toBe(State.CONNECTING)
  })
})

describe('reconcile: local states that win', () => {
  it.each([State.ERROR, State.ENDED, State.RECONNECTING])('%s is kept', (local) => {
    expect(reconcile(local, 'TRANSLATING')).toBe(local)
  })

  it('backend ENDED ends a live call', () => {
    expect(reconcile(State.TRANSLATING, 'ENDED')).toBe(State.ENDED)
  })

  it('no backend status changes nothing', () => {
    expect(reconcile(State.RINGING, undefined)).toBe(State.RINGING)
  })
})

describe('labels and end reasons', () => {
  it('names the ringing and answered states', () => {
    expect(label(State.CONNECTING)).toBe('Calling…')
    expect(label(State.RINGING)).toBe('Ringing…')
    expect(label(State.ANSWERED)).toBe('Answered')
  })

  it('knows when the other person has picked up', () => {
    expect(isAnswered(State.RINGING)).toBe(false)
    expect(isAnswered(State.ANSWERED)).toBe(true)
    expect(isAnswered(State.TRANSLATING)).toBe(true)
  })

  it.each([
    ['busy', 'The line was busy or the call was declined.'],
    ['declined', 'The call was declined.'],
    ['no-answer', 'No answer.'],
    ['failed', 'The number could not be reached.'],
    ['canceled', 'Call cancelled.'],
  ])('%s -> message', (reason, message) => {
    expect(endMessage(reason)).toBe(message)
  })

  it('an ordinary hang-up needs no explanation', () => {
    expect(endMessage('completed')).toBeNull()
    expect(endMessage('user_ended')).toBeNull()
    expect(endMessage(null)).toBeNull()
    expect(endShort('completed')).toBeNull()
  })
})
