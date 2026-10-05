import { describe, expect, it } from 'vitest'

import { captionsReducer, initialCaptions, isLive, LIVE_MS, stamp } from './captions'

const line = (id, speaker, text, extra = {}) => ({
  id, speaker, text, lang: 'en', from: speaker === 'them' ? 'es' : null, t: id, ...extra,
})

describe('captionsReducer', () => {
  it('a snapshot replaces the list, in id order', () => {
    const next = captionsReducer(initialCaptions, {
      type: 'snapshot', at: 5, lines: [line(2, 'them', 'Yes'), line(1, 'you', 'Hi')],
    })
    expect(next.map((l) => l.id)).toEqual([1, 2])
    expect(next.every((l) => l.at === 0)).toBe(true)   // old lines never glow
  })

  it('a reconnect snapshot keeps arrival times it already knew', () => {
    let s = captionsReducer(initialCaptions, { type: 'caption', at: 100, line: line(1, 'you', 'Hi') })
    s = captionsReducer(s, { type: 'snapshot', at: 999, lines: [line(1, 'you', 'Hi')] })
    expect(s[0].at).toBe(100)
  })

  it('an update replaces the line by id with its full text', () => {
    let s = captionsReducer(initialCaptions, { type: 'caption', at: 1, line: line(1, 'them', 'Yes') })
    s = captionsReducer(s, { type: 'caption', at: 2, line: line(1, 'them', 'Yes, at ten.') })
    expect(s).toHaveLength(1)
    expect(s[0]).toMatchObject({ text: 'Yes, at ten.', at: 2 })
  })

  it('a new line is appended in id order even if it arrives late', () => {
    let s = captionsReducer(initialCaptions, { type: 'caption', at: 1, line: line(3, 'you', 'c') })
    s = captionsReducer(s, { type: 'caption', at: 2, line: line(2, 'them', 'b') })
    expect(s.map((l) => l.id)).toEqual([2, 3])
  })

  it('an identical update keeps the same array, so React skips the render', () => {
    const s = captionsReducer(initialCaptions, { type: 'caption', at: 1, line: line(1, 'you', 'Hi') })
    expect(captionsReducer(s, { type: 'caption', at: 2, line: line(1, 'you', 'Hi') })).toBe(s)
  })

  it('reset clears everything for a new call', () => {
    const s = captionsReducer(initialCaptions, { type: 'caption', at: 1, line: line(1, 'you', 'Hi') })
    expect(captionsReducer(s, { type: 'reset' })).toEqual([])
  })
})

describe('isLive', () => {
  const lines = [
    { ...line(1, 'them', 'old'), at: 1000 },
    { ...line(2, 'you', 'mine'), at: 1000 },
    { ...line(3, 'them', 'new'), at: 1000 },
  ]

  it('only the newest line per speaker can be live', () => {
    expect(isLive(lines[2], lines, 1100)).toBe(true)
    expect(isLive(lines[0], lines, 1100)).toBe(false)
    expect(isLive(lines[1], lines, 1100)).toBe(true)
  })

  it('a line stops being live once updates stop', () => {
    expect(isLive(lines[2], lines, 1000 + LIVE_MS + 1)).toBe(false)
  })
})

describe('stamp', () => {
  it('formats seconds since the call started', () => {
    expect(stamp(0)).toBe('00:00')
    expect(stamp(64.9)).toBe('01:04')
    expect(stamp(-3)).toBe('00:00')
  })
})
