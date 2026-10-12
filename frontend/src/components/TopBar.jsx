import { isAdmin } from '../lib/auth'
import { Link } from '../lib/router'

/**
 * The bar across signed-in pages. Hidden on the live call screen, so nobody
 * leaves a call by accident.
 */
export function TopBar({ user, route, onSignOut }) {
  const at = (...names) => (names.includes(route.name) ? 'page' : undefined)
  return (
    <header className="topbar">
      <Link to="/" className="topbar__brand">Interpreter</Link>

      <nav className="topbar__nav" aria-label="Main">
        <Link to="/" aria-current={at('call')}>New call</Link>
        <Link to="/history" aria-current={at('history', 'historyCall')}>History</Link>
        {isAdmin(user) && (
          <Link to="/admin" aria-current={at('admin', 'adminCall')}>Admin</Link>
        )}
      </nav>

      <div className="topbar__user">
        <span className="topbar__who">
          {isAdmin(user) && <span className="topbar__role">Admin</span>}
          <span className="topbar__name" title={user.email}>{user.name}</span>
        </span>
        <button type="button" className="topbar__out" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </header>
  )
}
