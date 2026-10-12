import { useEffect, useState } from 'react'

/**
 * The few addresses this app has, and moving between them.
 *
 * Small on purpose: path + query, pushState, popstate. The call screen still
 * adds its own history entry at the same address (see App), so Back during a
 * call hangs up rather than leaving the page.
 */

const ROUTES = [
  ['login', /^\/login$/],
  ['request', /^\/request-access$/],
  ['call', /^\/$/],
  ['history', /^\/history$/],
  ['historyCall', /^\/history\/(\d+)$/],
  ['admin', /^\/admin$/],
  ['adminCall', /^\/admin\/calls\/(\d+)$/],
]

/** {name, id, query} for a path; unknown paths are 'notFound'. */
export function match(pathname, search = '') {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  const query = Object.fromEntries(new URLSearchParams(search))
  for (const [name, pattern] of ROUTES) {
    const m = path.match(pattern)
    if (m) return { name, id: m[1] ? Number(m[1]) : null, query, path }
  }
  return { name: 'notFound', id: null, query, path }
}

const EVENT = 'app:navigate'

export function navigate(to, { replace = false } = {}) {
  if (to === window.location.pathname + window.location.search) return
  window.history[replace ? 'replaceState' : 'pushState']({}, '', to)
  window.dispatchEvent(new Event(EVENT))
}

export function useRoute() {
  const read = () => match(window.location.pathname, window.location.search)
  const [route, setRoute] = useState(read)
  useEffect(() => {
    const update = () => {
      const next = read()
      setRoute((prev) => (prev.path === next.path &&
        JSON.stringify(prev.query) === JSON.stringify(next.query) ? prev : next))
    }
    window.addEventListener('popstate', update)
    window.addEventListener(EVENT, update)
    return () => {
      window.removeEventListener('popstate', update)
      window.removeEventListener(EVENT, update)
    }
  }, [])
  return route
}

/** An ordinary link that moves within the app without reloading it. */
export function Link({ to, children, ...rest }) {
  const onClick = (event) => {
    if (event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    navigate(to)
  }
  return (
    <a href={to} onClick={onClick} {...rest}>
      {children}
    </a>
  )
}
