/**
 * A saved call, read back in the order things were said.
 *
 * Lines arrive as {id, owner: 'A'|'B', kind: 'heard'|'spoke', lang, text, t}.
 * `heard` is what `owner` said, in their own language; `spoke` is the
 * translation of it the other person heard. Translations trail the words by
 * a few seconds and are cut into lines independently, so they are shown as
 * they came rather than force-paired with a sentence they may not match.
 *
 * The caller (A) is on the left and the person called (B) on the right, as
 * on the live call screen.
 */
export function transcriptRows(lines) {
  return [...lines]
    .sort((a, b) => a.t - b.t || a.id - b.id)
    .map((line) => ({
      ...line,
      side: line.owner === 'A' ? 'caller' : 'callee',
      translation: line.kind === 'spoke',
    }))
}
