import { useEffect, useRef } from 'react'

import { isLive, stamp } from '../lib/captions'
import { labelOf } from '../lib/languages'

/**
 * Live captions. Every line is in the viewer's language: "You said" is what
 * the model heard you say; "They said" is the translation you are hearing.
 *
 * Your lines sit under your pane on the left, theirs under theirs on the
 * right, mirroring the party strip above.
 */
export function Captions({ lines, now, language, catalogue = [] }) {
  const list = useRef(null)
  const stick = useRef(true)

  // Follow new lines, unless the reader has scrolled up to look back.
  useEffect(() => {
    const el = list.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [lines])

  const onScroll = () => {
    const el = list.current
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }

  return (
    <section className="captions" aria-label="Live captions">
      <header className="captions__head">
        <span className="captions__title">Live captions</span>
        <span className="captions__lang">in {labelOf(language, catalogue)}</span>
      </header>

      <div className="captions__list" ref={list} onScroll={onScroll} aria-live="polite">
        {lines.length === 0 ? (
          <p className="captions__empty">
            What you say, and what you hear, will appear here as text.
          </p>
        ) : (
          lines.map((line) => {
            const live = isLive(line, lines, now)
            const you = line.speaker === 'you'
            const pressed = line.kind === 'pressed'

            // Keys pressed on a menu are not speech: no language, no
            // translation, and nothing to mistake for something said.
            if (pressed) {
              return (
                <article key={line.id} className="cap cap--you cap--pressed">
                  <div className="cap__meta">
                    <span>You pressed</span>
                    <time>{stamp(line.t)}</time>
                  </div>
                  <p className="cap__text cap__keys">{line.text}</p>
                </article>
              )
            }

            return (
              <article
                key={line.id}
                className={`cap cap--${you ? 'you' : 'them'}${live ? ' cap--live' : ''}`}
              >
                <div className="cap__meta">
                  {you && <span>You said</span>}
                  <time>{stamp(line.t)}</time>
                  {!you && (live ? (
                    <span className="cap__live">Speaking</span>
                  ) : (
                    <span>
                      They said
                      {line.from && ` · from ${labelOf(line.from, catalogue)}`}
                    </span>
                  ))}
                </div>
                <p className="cap__text" lang={line.lang}>
                  {line.text}
                  {live && <span className="cap__caret" aria-hidden="true" />}
                </p>
              </article>
            )
          })
        )}
      </div>
    </section>
  )
}
