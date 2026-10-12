import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { actionsFor } from './AdminPage'
import { LoginScreen, RequestAccessScreen } from './AuthScreens'
import { CallDetail, CallsTable } from './Calls'
import { TopBar } from './TopBar'

const catalogue = [
  { code: 'en', label: 'English', native: 'English' },
  { code: 'ta', label: 'Tamil', native: 'தமிழ்' },
]

const call = {
  id: 9,
  user: { id: 2, name: 'Priya', email: 'priya@example.com' },
  phone_number: '+919876543210',
  source_lang: 'en',
  target_lang: 'ta',
  status: 'ended',
  end_reason: 'user_ended',
  started_at: '2026-10-12T09:30:00Z',
  duration_s: 125,
  model_usd: 0.0123,
  lines: 2,
  transcript: [
    { id: 1, owner: 'A', kind: 'heard', lang: 'en', text: 'Hello there', t: 1 },
    { id: 2, owner: 'A', kind: 'spoke', lang: 'ta', text: 'வணக்கம்', t: 3 },
  ],
}

describe('sign-in pages', () => {
  it('sign in asks for email and password, with password-manager hints', () => {
    const html = renderToStaticMarkup(<LoginScreen onSignedIn={() => {}} />)
    expect(html).toContain('autoComplete="username"')
    expect(html).toContain('autoComplete="current-password"')
    expect(html).toContain('Show password')
    expect(html).toContain('href="/request-access"')
    expect(html).toContain('Ask your administrator')
  })

  it('request access asks for name, email and a new password', () => {
    const html = renderToStaticMarkup(<RequestAccessScreen />)
    expect(html).toContain('autoComplete="name"')
    expect(html).toContain('autoComplete="email"')
    expect(html).toContain('autoComplete="new-password"')
    expect(html).toContain('At least 12 characters')
    expect(html).toContain('Request access')
    expect(html).not.toContain('Sign up')
  })
})

describe('top bar', () => {
  const route = { name: 'history' }
  it('shows Admin only to admins', () => {
    const member = renderToStaticMarkup(
      <TopBar user={{ name: 'Priya', email: 'p@x', role: 'user' }} route={route}
              onSignOut={() => {}} />)
    const admin = renderToStaticMarkup(
      <TopBar user={{ name: 'Arun', email: 'a@x', role: 'admin' }} route={route}
              onSignOut={() => {}} />)
    expect(member).not.toContain('href="/admin"')
    expect(admin).toContain('href="/admin"')
    expect(member).toContain('aria-current="page"')
  })
})

describe('calls', () => {
  it('lists calls with a link to each, and the member only for admins', () => {
    const mine = renderToStaticMarkup(
      <CallsTable calls={[call]} catalogue={catalogue} linkFor={(c) => `/history/${c.id}`} />)
    expect(mine).toContain('href="/history/9"')
    expect(mine).toContain('English → Tamil')
    expect(mine).toContain('2:05')
    expect(mine).toContain('Completed')
    expect(mine).not.toContain('priya@example.com')

    const all = renderToStaticMarkup(
      <CallsTable calls={[call]} catalogue={catalogue} showUser
                  linkFor={(c) => `/admin/calls/${c.id}`} />)
    expect(all).toContain('priya@example.com')
  })

  it('explains an empty list', () => {
    const html = renderToStaticMarkup(
      <CallsTable calls={[]} catalogue={catalogue} linkFor={() => '/'} />)
    expect(html).toContain('No calls yet')
  })

  it('reads a call back with the words and their translation', () => {
    const html = renderToStaticMarkup(
      <CallDetail call={call} catalogue={catalogue}
                  back={{ to: '/history', label: 'All calls' }} />)
    expect(html).toContain('Hello there')
    expect(html).toContain('translated to Tamil')
    expect(html).toContain('cap--translation')
    expect(html).toContain('lang="ta"')
    expect(html).not.toContain('Model cost')
  })
})

describe('admin actions', () => {
  const me = { id: 1 }
  it('offers what makes sense for each member, and nothing on yourself or the owner', () => {
    expect(actionsFor({ id: 2, status: 'pending', role: 'user' }, me)).toEqual(['approve', 'reject'])
    expect(actionsFor({ id: 2, status: 'active', role: 'user' }, me)).toEqual(['promote', 'disable'])
    expect(actionsFor({ id: 2, status: 'active', role: 'admin' }, me)).toEqual(['demote', 'disable'])
    expect(actionsFor({ id: 2, status: 'disabled', role: 'user' }, me)).toEqual(['enable'])
    expect(actionsFor({ id: 2, status: 'rejected', role: 'user' }, me)).toEqual([])
    expect(actionsFor({ id: 1, status: 'active', role: 'admin' }, me)).toEqual([])
    expect(actionsFor({ id: 3, status: 'active', role: 'admin', owner: true }, me)).toEqual([])
  })
})
