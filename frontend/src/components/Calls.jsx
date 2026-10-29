import { duration, outcome, when } from '../lib/format'
import { labelOf } from '../lib/languages'
import { Link } from '../lib/router'
import { transcriptRows } from '../lib/transcript'
import { Empty, Status } from './ui'

/**
 * Past calls, as a list and as one call read back. Shared by a member's own
 * History and the admin's view of everyone's calls (`showUser`).
 */
export function CallsTable({ calls, catalogue, showUser = false, linkFor, empty }) {
  if (!calls.length) return empty ?? <Empty title="No calls yet" />
  return (
    <div className="table" role="table" aria-label="Calls">
      <div className={`table__row table__head${showUser ? ' table--user' : ''}`} role="row">
        <span role="columnheader">When</span>
        {showUser && <span role="columnheader">Member</span>}
        <span role="columnheader">Number</span>
        <span role="columnheader">Languages</span>
        <span role="columnheader" className="num">Length</span>
        <span role="columnheader">Outcome</span>
      </div>
      {calls.map((call) => {
        const result = outcome(call)
        return (
          <Link
            key={call.id}
            to={linkFor(call)}
            className={`table__row table__link${showUser ? ' table--user' : ''}`}
            role="row"
          >
            <span role="cell" className="mono" data-label="When">{when(call.started_at)}</span>
            {showUser && (
              <span role="cell" className="table__who">
                {call.user.name}
                <small>{call.user.email}</small>
              </span>
            )}
            <span role="cell" className="mono" data-label="Number">
              {call.phone_number ?? 'Demo call'}
            </span>
            <span role="cell" data-label="Languages">
              {labelOf(call.source_lang, catalogue)} → {labelOf(call.target_lang, catalogue)}
            </span>
            <span role="cell" className="mono num" data-label="Length">
              {duration(call.duration_s)}
            </span>
            <span role="cell" data-label="Outcome">
              <Status tone={result.tone}>{result.label}</Status>
              {call.lines > 0 && <small className="table__lines">{call.lines} lines</small>}
            </span>
          </Link>
        )
      })}
    </div>
  )
}

export function CallDetail({ call, catalogue, showUser = false, back }) {
  const result = outcome(call)
  const rows = transcriptRows(call.transcript ?? [])
  const lang = (code) => labelOf(code, catalogue)
  return (
    <article className="detail enter">
      <Link to={back.to} className="detail__back">← {back.label}</Link>

      <header className="detail__head">
        <p className="eyebrow">{when(call.started_at)}</p>
        <h1 className="detail__title">
          {lang(call.source_lang)} <span aria-hidden="true">⇄</span>{' '}
          {lang(call.target_lang)}
        </h1>
        <dl className="detail__facts">
          {showUser && (
            <div><dt>Member</dt><dd>{call.user.name} · {call.user.email}</dd></div>
          )}
          <div><dt>Number</dt><dd className="mono">{call.phone_number ?? 'Demo call'}</dd></div>
          <div><dt>Length</dt><dd className="mono">{duration(call.duration_s)}</dd></div>
          <div><dt>Outcome</dt><dd><Status tone={result.tone}>{result.label}</Status></dd></div>
          {showUser && (
            <div><dt>Model cost</dt><dd className="mono">${call.model_usd.toFixed(4)}</dd></div>
          )}
        </dl>
      </header>

      <section className="captions transcript" aria-label="Transcript">
        <header className="captions__head">
          <span className="captions__title">Transcript</span>
          <span className="captions__lang">
            Caller in {lang(call.source_lang)} · they in {lang(call.target_lang)}
          </span>
        </header>
        <div className="captions__list">
          {rows.length === 0 ? (
            <p className="captions__empty">
              Nothing was transcribed on this call.
            </p>
          ) : rows.map((line) => (
            <article
              key={`${line.owner}${line.id}`}
              className={`cap cap--${line.side === 'caller' ? 'you' : 'them'}` +
                (line.translation ? ' cap--translation' : '') +
                (line.pressed ? ' cap--pressed' : '')}
            >
              <div className="cap__meta">
                <span>
                  {line.side === 'caller' ? 'Caller' : 'They'}
                  {line.pressed && ' pressed'}
                  {line.translation && ` · translated to ${lang(line.lang)}`}
                  {!line.pressed && !line.translation && ' said'}
                </span>
                <time>{duration(line.t)}</time>
              </div>
              <p
                className={`cap__text${line.pressed ? ' cap__keys' : ''}`}
                lang={line.lang || undefined}
              >
                {line.text}
              </p>
            </article>
          ))}
        </div>
      </section>
    </article>
  )
}
