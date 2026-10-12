import { useState } from 'react'

import { useLoad } from '../hooks/useLoad'
import { api } from '../lib/api'
import { CallDetail, CallsTable } from './Calls'
import { Alert, Button, Empty } from './ui'

export const PAGE = 25

/** A member's own calls, and one of them read back. */
export function HistoryPage({ route, catalogue }) {
  if (route.name === 'historyCall') {
    return <OneCall id={route.id} catalogue={catalogue} />
  }
  return <MyCalls catalogue={catalogue} />
}

function MyCalls({ catalogue }) {
  const [page, setPage] = useState(0)
  const { data, error, loading } = useLoad(
    () => api.myCalls({ limit: PAGE, offset: page * PAGE }), [page])

  return (
    <section className="page enter">
      <header className="page__head">
        <p className="eyebrow">Your calls</p>
        <h1 className="page__title">Call <em>history</em></h1>
        <p className="lede">Every call you place is saved here with its full transcript.</p>
      </header>

      <Alert>{error?.message}</Alert>
      {data ? (
        <>
          <CallsTable
            calls={data.items}
            catalogue={catalogue}
            linkFor={(c) => `/history/${c.id}`}
            empty={
              <Empty title="No calls yet">
                Your translated calls will appear here, with everything that was said.
              </Empty>
            }
          />
          <Pager page={page} total={data.total} onPage={setPage} />
        </>
      ) : loading && <p className="page__loading">Loading your calls…</p>}
    </section>
  )
}

function OneCall({ id, catalogue }) {
  const { data, error } = useLoad(() => api.myCall(id), [id])
  return (
    <section className="page">
      {error ? (
        <Empty title="Call not found">
          It may have been removed, or it belongs to someone else.
        </Empty>
      ) : data ? (
        <CallDetail call={data} catalogue={catalogue}
                    back={{ to: '/history', label: 'All calls' }} />
      ) : <p className="page__loading">Loading…</p>}
    </section>
  )
}

export function Pager({ page, total, onPage }) {
  const pages = Math.ceil(total / PAGE)
  if (pages <= 1) return null
  return (
    <nav className="pager" aria-label="Pages">
      <Button variant="ghost" disabled={page === 0} onClick={() => onPage(page - 1)}>
        ← Newer
      </Button>
      <span className="pager__where mono">{page + 1} / {pages}</span>
      <Button variant="ghost" disabled={page + 1 >= pages} onClick={() => onPage(page + 1)}>
        Older →
      </Button>
    </nav>
  )
}
