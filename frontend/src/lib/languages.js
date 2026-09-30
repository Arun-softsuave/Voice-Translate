/**
 * Language helpers.
 *
 * There is deliberately no list here any more. The backend decides which
 * languages are available, and the two directions differ: the translate model
 * accepts far more source languages than it can produce as targets. Keeping a
 * copy in the frontend meant the two could drift, and the UI could offer a
 * target the model cannot speak — a failure that only shows up mid-call.
 *
 * The lists arrive from GET /health as {source: [...], target: [...]}, each
 * entry {code, label, native}.
 */

/**
 * English name for a code.
 *
 * Falls back to the raw code rather than throwing: a language that has been
 * gated out should degrade to showing "te" on the call screen, not crash it.
 */
export function labelOf(code, catalogue = []) {
  return catalogue.find((l) => l.code === code)?.label ?? code
}

/** First entry of a list, used to pick a sensible default. */
export const firstCode = (list, fallback = null) => list?.[0]?.code ?? fallback

/** Whether a code may be used in a given direction. */
export const allows = (list, code) => Boolean(list?.some((l) => l.code === code))
