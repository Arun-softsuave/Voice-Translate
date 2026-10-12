import { describe, expect, it } from 'vitest'

import { home, redirectFor, safeNext } from './auth'
import { duration, outcome } from './format'
import { match } from './router'
import { transcriptRows } from './transcript'

const member = { id: 2, name: 'Priya', role: 'user' }
const admin = { id: 1, name: 'Arun', role: 'admin' }
const at = (url) => {
  const [path, search = ''] = url.split('?')
  return match(path, search)
}

describe('match', () => {
  it('knows every page and its id', () => {
    expect(at('/').name).toBe('call')
    expect(at('/login').name).toBe('login')
    expect(at('/request-access').name).toBe('request')
    expect(at('/history').name).toBe('history')
    expect(at('/history/12')).toMatchObject({ name: 'historyCall', id: 12 })
    expect(at('/admin?tab=calls')).toMatchObject({ name: 'admin', query: { tab: 'calls' } })
    expect(at('/admin/calls/7')).toMatchObject({ name: 'adminCall', id: 7 })
  })

  it('ignores a trailing slash and rejects anything else', () => {
    expect(at('/history/').name).toBe('history')
    expect(at('/history/abc').name).toBe('notFound')
    expect(at('/wp-admin').name).toBe('notFound')
  })
})

describe('redirectFor', () => {
  it('sends a signed-out visitor to sign in, remembering where they were going', () => {
    expect(redirectFor(at('/'), null)).toBe('/login')
    expect(redirectFor(at('/history/12'), null)).toBe('/login?next=%2Fhistory%2F12')
    expect(redirectFor(at('/admin?tab=calls'), null))
      .toBe('/login?next=%2Fadmin%3Ftab%3Dcalls')
    expect(redirectFor(at('/login'), null)).toBeNull()
    expect(redirectFor(at('/request-access'), null)).toBeNull()
  })

  it('takes a signed-in person away from the sign-in pages', () => {
    expect(redirectFor(at('/login'), member)).toBe('/')
    expect(redirectFor(at('/login'), admin)).toBe('/admin')
    expect(redirectFor(at('/request-access'), member)).toBe('/')
    expect(redirectFor(at('/login?next=/history/3'), member)).toBe('/history/3')
  })

  it('keeps members out of the admin pages', () => {
    expect(redirectFor(at('/admin'), member)).toBe('/')
    expect(redirectFor(at('/admin/calls/4'), member)).toBe('/')
    expect(redirectFor(at('/admin'), admin)).toBeNull()
    expect(redirectFor(at('/history'), member)).toBeNull()
  })

  it('sends unknown pages home', () => {
    expect(redirectFor(at('/nope'), member)).toBe('/')
    expect(redirectFor(at('/nope'), null)).toBe('/login')
  })
})

describe('safeNext', () => {
  it('only follows our own pages that this person may see', () => {
    expect(safeNext('/history/3', member)).toBe('/history/3')
    expect(safeNext('/admin?tab=members', admin)).toBe('/admin?tab=members')
    expect(safeNext('/admin', member)).toBeNull()
    expect(safeNext('//evil.example/x', member)).toBeNull()
    expect(safeNext('https://evil.example', member)).toBeNull()
    expect(safeNext('/login', member)).toBeNull()
    expect(safeNext(undefined, member)).toBeNull()
  })

  it('lands admins on the admin page and members on a new call', () => {
    expect(home(admin)).toBe('/admin')
    expect(home(member)).toBe('/')
  })
})

describe('format', () => {
  it('writes call lengths the way a clock does', () => {
    expect(duration(0)).toBe('0:00')
    expect(duration(42.4)).toBe('0:42')
    expect(duration(725)).toBe('12:05')
    expect(duration(3729)).toBe('1:02:09')
    expect(duration(null)).toBe('—')
  })

  it('names how a call went', () => {
    expect(outcome({ status: 'in_progress' }).label).toBe('In progress')
    expect(outcome({ status: 'failed', end_reason: 'twilio_21215' }).label).toBe('Not placed')
    expect(outcome({ status: 'ended', end_reason: 'user_ended', duration_s: 30 }))
      .toEqual({ label: 'Completed', tone: 'idle' })
    expect(outcome({ status: 'ended', end_reason: 'no-answer', duration_s: null }))
      .toEqual({ label: 'No answer', tone: 'wait' })
    expect(outcome({ status: 'ended', end_reason: 'user_disabled' }).label)
      .toBe('Ended by admin')
  })
})

describe('transcriptRows', () => {
  it('reads a call back in the order it was said, caller left, callee right', () => {
    const rows = transcriptRows([
      { id: 3, owner: 'A', kind: 'spoke', lang: 'ta', text: 'வணக்கம்', t: 2.5 },
      { id: 1, owner: 'A', kind: 'heard', lang: 'en', text: 'Hello', t: 0.4 },
      { id: 4, owner: 'B', kind: 'heard', lang: 'ta', text: 'ஆமாம்', t: 4 },
    ])
    expect(rows.map((r) => [r.text, r.side, r.translation])).toEqual([
      ['Hello', 'caller', false],
      ['வணக்கம்', 'caller', true],
      ['ஆமாம்', 'callee', false],
    ])
  })
})
