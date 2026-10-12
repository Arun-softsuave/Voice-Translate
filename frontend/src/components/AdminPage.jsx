import { useMemo, useState } from 'react'

import { useLoad } from '../hooks/useLoad'
import { api } from '../lib/api'
import { dayStart, when } from '../lib/format'
import { navigate } from '../lib/router'
import { CallDetail, CallsTable } from './Calls'
import { ConfirmDialog } from './ConfirmDialog'
import { PAGE, Pager } from './HistoryPage'
import { Alert, Button, Empty, Input, Select, Status, Toast } from './ui'

const TABS = [
  ['requests', 'Requests'],
  ['members', 'Members'],
  ['calls', 'All calls'],
]

/**
 * Who may use the app, and every call placed with it. The server checks the
 * admin role on every one of these requests; this page only arranges them.
 */
export function AdminPage({ route, user, catalogue }) {
  if (route.name === 'adminCall') {
    return <AdminCall id={route.id} catalogue={catalogue} />
  }
  const tab = TABS.some(([key]) => key === route.query.tab) ? route.query.tab : 'requests'
  return <Dashboard tab={tab} me={user} catalogue={catalogue} />
}

function Dashboard({ tab, me, catalogue }) {
  const users = useLoad(() => api.adminUsers(), [])
  const pending = users.data?.counts.pending ?? 0

  return (
    <section className="page enter">
      <header className="page__head">
        <p className="eyebrow">Administration</p>
        <h1 className="page__title">Access &amp; <em>calls</em></h1>
      </header>

      <nav className="tabs" role="tablist" aria-label="Admin sections">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className="tabs__tab"
            onClick={() => navigate(`/admin?tab=${key}`, { replace: true })}
          >
            {label}
            {key === 'requests' && pending > 0 && (
              <span className="tabs__count" aria-label={`${pending} waiting`}>{pending}</span>
            )}
          </button>
        ))}
      </nav>

      <Alert>{users.error?.message}</Alert>
      {tab === 'calls' ? (
        <AllCalls catalogue={catalogue} />
      ) : users.data ? (
        <Members
          requests={tab === 'requests'}
          users={users.data.items}
          me={me}
          onChanged={users.reload}
        />
      ) : users.loading && <p className="page__loading">Loading members…</p>}
    </section>
  )
}

// What each action does, how it is confirmed, and what to say afterwards.
const ACTIONS = {
  approve: { label: 'Approve', done: (u) => `${u.name} can now sign in.` },
  reject: {
    label: 'Reject', tone: 'danger',
    ask: (u) => ({
      title: `Reject ${u.name}'s request?`,
      body: `${u.email} won't be able to sign in. They can ask again later.`,
      confirm: 'Reject request',
    }),
    done: (u) => `${u.name}'s request was rejected.`,
  },
  disable: {
    label: 'Disable', tone: 'danger',
    ask: (u) => ({
      title: `Turn off ${u.name}'s access?`,
      body: `${u.name} will be signed out at once, and any call they are on will end. You can turn access back on later.`,
      confirm: 'Disable access',
    }),
    done: (u) => `${u.name}'s access is off.`,
  },
  enable: { label: 'Enable', done: (u) => `${u.name} can sign in again.` },
  promote: {
    label: 'Make admin', tone: 'ghost',
    ask: (u) => ({
      title: `Make ${u.name} an administrator?`,
      body: `${u.name} will see every member and every call, and can approve or turn off access.`,
      confirm: 'Make admin',
    }),
    done: (u) => `${u.name} is now an administrator.`,
  },
  demote: {
    label: 'Remove admin', tone: 'danger',
    ask: (u) => ({
      title: `Remove ${u.name}'s admin rights?`,
      body: `${u.name} will be signed out and, after signing in again, will see only their own calls.`,
      confirm: 'Remove admin',
    }),
    done: (u) => `${u.name} is no longer an administrator.`,
  },
}

/** Which actions a member's row offers. */
export function actionsFor(user, me) {
  if (user.id === me.id || user.owner) return []
  switch (user.status) {
    case 'pending': return ['approve', 'reject']
    case 'active': return [user.role === 'admin' ? 'demote' : 'promote', 'disable']
    case 'disabled': return ['enable']
    default: return []
  }
}

const STATUS_TONE = { active: 'live', pending: 'wait', disabled: 'error', rejected: 'idle' }

function Members({ requests, users, me, onChanged }) {
  const [filter, setFilter] = useState('all')
  const [q, setQ] = useState('')
  const [asking, setAsking] = useState(null)       // {user, action}
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState(null)         // {tone, text}

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return users
      .filter((u) => (requests ? u.status === 'pending'
        : u.status !== 'pending' && (filter === 'all' || u.status === filter)))
      .filter((u) => !needle || u.name.toLowerCase().includes(needle) ||
        u.email.toLowerCase().includes(needle))
  }, [users, requests, filter, q])

  const run = async (target, action) => {
    setBusy(true)
    try {
      await api.adminAct(target.id, action)
      setToast({ tone: 'info', text: ACTIONS[action].done(target) })
    } catch (error) {
      setToast({ tone: 'error', text: error.message })
    } finally {
      setBusy(false)
      setAsking(null)
      onChanged()
    }
  }

  const start = (target, action) =>
    (ACTIONS[action].ask ? setAsking({ user: target, action }) : run(target, action))

  const question = asking && ACTIONS[asking.action].ask(asking.user)

  return (
    <>
      <div className="filters">
        <Input
          type="search"
          placeholder="Search name or email"
          aria-label="Search members"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {!requests && (
          <Select aria-label="Show" value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">Everyone</option>
            <option value="active">Active</option>
            <option value="disabled">Disabled</option>
            <option value="rejected">Rejected</option>
          </Select>
        )}
      </div>

      {shown.length === 0 ? (
        requests ? (
          <Empty title="No requests waiting">
            When someone asks for access, they appear here for you to approve.
          </Empty>
        ) : <Empty title="No members match">Try another search or filter.</Empty>
      ) : (
        <div className="table" role="table" aria-label={requests ? 'Access requests' : 'Members'}>
          <div className="table__row table__head table--members" role="row">
            <span role="columnheader">Member</span>
            <span role="columnheader">{requests ? 'Asked' : 'Status'}</span>
            <span role="columnheader" className="num">{requests ? '' : 'Calls'}</span>
            <span role="columnheader">{requests ? '' : 'Last call'}</span>
            <span role="columnheader" className="table__actions-head">Actions</span>
          </div>
          {shown.map((u) => (
            <div key={u.id} className="table__row table--members" role="row">
              <span role="cell" className="table__who">
                <span className="table__name">
                  {u.name}
                  {u.role === 'admin' && <span className="tag">Admin</span>}
                  {u.id === me.id && <span className="tag tag--quiet">You</span>}
                </span>
                <small>{u.email}</small>
              </span>
              <span role="cell" data-label={requests ? 'Asked' : 'Status'}>
                {requests
                  ? <span className="mono">{when(u.created_at)}</span>
                  : <Status tone={STATUS_TONE[u.status]}>{u.status}</Status>}
              </span>
              <span role="cell" className="mono num" data-label={requests ? null : 'Calls'}>
                {requests ? '' : u.calls}
              </span>
              <span role="cell" className="mono" data-label={requests ? null : 'Last call'}>
                {requests ? '' : when(u.last_call_at)}
              </span>
              <span role="cell" className="table__actions">
                {actionsFor(u, me).map((action) => (
                  <Button
                    key={action}
                    variant={ACTIONS[action].tone === 'danger' ? 'danger' : 'ghost'}
                    active={action === 'approve' || action === 'enable'}
                    small
                    disabled={busy}
                    onClick={() => start(u, action)}
                  >
                    {ACTIONS[action].label}
                  </Button>
                ))}
                {u.owner && <small className="table__note">Owner</small>}
              </span>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={Boolean(asking)}
        title={question?.title}
        confirm={question?.confirm}
        tone={asking && ACTIONS[asking.action].tone === 'danger' ? 'danger' : 'ghost'}
        busy={busy}
        onCancel={() => setAsking(null)}
        onConfirm={() => run(asking.user, asking.action)}
      >
        <p>{question?.body}</p>
      </ConfirmDialog>

      <Toast tone={toast?.tone} message={toast?.text} onClose={() => setToast(null)} />
    </>
  )
}

function AllCalls({ catalogue }) {
  const [q, setQ] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [page, setPage] = useState(0)
  const { data, error, loading } = useLoad(
    () => api.adminCalls({ q: q.trim(), from: dayStart(from), to: dayStart(to, 1),
                           limit: PAGE, offset: page * PAGE }),
    [q, from, to, page])
  const filtered = Boolean(q || from || to)
  const change = (setter) => (e) => {
    setter(e.target.value)
    setPage(0)
  }

  return (
    <>
      <div className="filters">
        <Input type="search" placeholder="Search member or number" aria-label="Search calls"
               value={q} onChange={change(setQ)} />
        <label className="filters__date">
          <span>From</span>
          <Input type="date" value={from} max={to || undefined} onChange={change(setFrom)} />
        </label>
        <label className="filters__date">
          <span>To</span>
          <Input type="date" value={to} min={from || undefined} onChange={change(setTo)} />
        </label>
        {filtered && (
          <button type="button" className="filters__clear" onClick={() => {
            setQ(''); setFrom(''); setTo(''); setPage(0)
          }}>Clear</button>
        )}
      </div>

      <Alert>{error?.message}</Alert>
      {data ? (
        <>
          <CallsTable
            calls={data.items}
            catalogue={catalogue}
            showUser
            linkFor={(c) => `/admin/calls/${c.id}`}
            empty={filtered
              ? <Empty title="No calls match">Try a wider date range or another search.</Empty>
              : <Empty title="No calls yet">Every call members place will appear here.</Empty>}
          />
          <Pager page={page} total={data.total} onPage={setPage} />
        </>
      ) : loading && <p className="page__loading">Loading calls…</p>}
    </>
  )
}

function AdminCall({ id, catalogue }) {
  const { data, error } = useLoad(() => api.adminCall(id), [id])
  return (
    <section className="page">
      {error ? (
        <Empty title="Call not found" />
      ) : data ? (
        <CallDetail call={data} catalogue={catalogue} showUser
                    back={{ to: '/admin?tab=calls', label: 'All calls' }} />
      ) : <p className="page__loading">Loading…</p>}
    </section>
  )
}
