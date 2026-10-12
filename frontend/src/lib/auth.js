import { match } from './router'

/**
 * Where a person may be, given who they are. Pure, so it is easy to test.
 *
 * The server enforces all of this on every request; these rules only keep
 * the page from showing a screen whose data the server would refuse.
 */

const PUBLIC = new Set(['login', 'request'])
const ADMIN_ONLY = new Set(['admin', 'adminCall'])

export const isAdmin = (user) => user?.role === 'admin'

/** Where someone lands after signing in. */
export const home = (user) => (isAdmin(user) ? '/admin' : '/')

/** `next` from the login URL, if it is one of our own pages this user may see. */
export function safeNext(next, user) {
  if (!next || !next.startsWith('/') || next.startsWith('//')) return null
  const [path, search = ''] = next.split('?')
  const route = match(path, search)
  if (route.name === 'notFound' || PUBLIC.has(route.name)) return null
  if (ADMIN_ONLY.has(route.name) && !isAdmin(user)) return null
  return next
}

/** The address to send the browser to instead, or null to stay. */
export function redirectFor(route, user) {
  if (!user) {
    if (PUBLIC.has(route.name)) return null
    const here = route.path + (Object.keys(route.query).length
      ? `?${new URLSearchParams(route.query)}` : '')
    return route.name === 'call' || route.name === 'notFound'
      ? '/login'
      : `/login?next=${encodeURIComponent(here)}`
  }
  if (PUBLIC.has(route.name)) return safeNext(route.query.next, user) ?? home(user)
  if (route.name === 'notFound') return home(user)
  if (ADMIN_ONLY.has(route.name) && !isAdmin(user)) return '/'
  return null
}
