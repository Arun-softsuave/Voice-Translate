/**
 * The two moments on a call that deserve a sound.
 *
 * Until now the only sign that someone picked up was a word changing on
 * screen. On a real call the caller missed it and said nothing for twelve
 * seconds; the person who had answered heard silence and hung up. A short
 * tone is what every phone in the world uses to say "go ahead".
 *
 * Synthesised rather than loaded from a file: two notes need no asset, no
 * network request and no decoding delay at the moment they are wanted.
 */

let ctx = null

/** The shared audio context, created on first use. */
function audio() {
  if (ctx) return ctx
  const Ctx = window.AudioContext || window.webkitAudioContext
  if (!Ctx) return null
  try {
    ctx = new Ctx()
  } catch {
    return null                      // no audio output on this machine
  }
  return ctx
}

/**
 * Play a sequence of short notes.
 * Each is [frequency in Hz, start offset in seconds, length in seconds].
 */
function play(notes, volume) {
  const c = audio()
  if (!c) return
  // Browsers suspend the context until a gesture; starting a call is one, but
  // it can still be suspended after a tab switch.
  if (c.state === 'suspended') c.resume().catch(() => {})

  const now = c.currentTime
  for (const [hz, at, length] of notes) {
    const osc = c.createOscillator()
    const gain = c.createGain()
    osc.type = 'sine'
    osc.frequency.value = hz

    // Fade each note in and out. A square-edged note clicks.
    const start = now + at
    const end = start + length
    gain.gain.setValueAtTime(0, start)
    gain.gain.linearRampToValueAtTime(volume, start + 0.015)
    gain.gain.setValueAtTime(volume, end - 0.04)
    gain.gain.linearRampToValueAtTime(0, end)

    osc.connect(gain).connect(c.destination)
    osc.start(start)
    osc.stop(end + 0.02)
  }
}

/** They picked up and the line is open: start talking. */
export function answered() {
  play([[660, 0, 0.12], [880, 0.13, 0.18]], 0.12)
}

/** The call is over. Lower and falling, so it cannot be mistaken for the above. */
export function ended() {
  play([[520, 0, 0.14], [390, 0.15, 0.2]], 0.09)
}
