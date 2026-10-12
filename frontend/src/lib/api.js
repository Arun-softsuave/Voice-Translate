// A production build is served by the backend itself, so it calls its own
// origin (''), whatever address that is. The Vite dev server on :5173 calls
// the backend on :8000 directly.
const DEFAULT_BASE = import.meta.env.DEV ? 'http://localhost:8000' : ''
const BASE = (import.meta.env.VITE_API_URL ?? DEFAULT_BASE).replace(/\/$/, '')

/**
 * The backend returns {error: {code, message}} for every failure and never
 * leaks internals, so `message` is safe to show the user directly.
 */
export class ApiError extends Error {
  constructor(code, message, status) {
    super(message)
    this.code = code
    this.status = status
  }
}

// Called when the server says the sign-in is gone (expired, signed out
// elsewhere, or the account was turned off). App sends the user to /login.
let onSignedOut = () => {}
export const whenSignedOut = (handler) => {
  onSignedOut = handler
}

async function request(path, { method = 'GET', body, quiet401 = false } = {}) {
  let response
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      // The sign-in cookie. Same origin in production; Vite on :5173 in dev.
      credentials: 'include',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError('NETWORK', 'Cannot reach the server. Is the backend running?', 0)
  }

  if (response.status === 204) return null

  let payload = null
  try {
    payload = await response.json()
  } catch {
    /* empty or non-JSON body */
  }

  if (response.status === 401 && !quiet401) onSignedOut()

  if (!response.ok) {
    const detail = payload?.detail ?? payload?.error ?? {}
    // FastAPI validation errors arrive as a list of issues
    const message =
      typeof detail === 'string'
        ? detail
        : detail.message ??
          (Array.isArray(detail) ? detail[0]?.msg : null) ??
          'Something went wrong.'
    throw new ApiError(detail.code ?? 'ERROR', message, response.status)
  }

  return payload
}

const query = (params = {}) => {
  const q = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''),
  ).toString()
  return q ? `?${q}` : ''
}

export const api = {
  health: () => request('/health'),

  // --- signing in -------------------------------------------------------
  /** The signed-in user, or null. Never triggers the signed-out handler. */
  me: () =>
    request('/api/auth/me', { quiet401: true })
      .then((r) => r.user)
      .catch((e) => {
        if (e.status === 401) return null
        throw e
      }),

  login: (email, password) =>
    request('/api/auth/login', {
      method: 'POST', body: { email, password }, quiet401: true,
    }).then((r) => r.user),

  logout: () => request('/api/auth/logout', { method: 'POST', quiet401: true }),

  requestAccess: ({ name, email, password }) =>
    request('/api/auth/request-access', {
      method: 'POST', body: { name, email, password }, quiet401: true,
    }),

  // --- history ----------------------------------------------------------
  myCalls: (params) => request(`/api/calls${query(params)}`),
  myCall: (id) => request(`/api/calls/${encodeURIComponent(id)}`),

  // --- admin ------------------------------------------------------------
  adminUsers: (params) => request(`/api/admin/users${query(params)}`),
  adminAct: (userId, action) =>
    request(`/api/admin/users/${encodeURIComponent(userId)}/${action}`, { method: 'POST' }),
  adminCalls: (params) => request(`/api/admin/calls${query(params)}`),
  adminCall: (id) => request(`/api/admin/calls/${encodeURIComponent(id)}`),

  // --- calls ------------------------------------------------------------
  // The server uses the signed-in user as the Twilio identity.
  token: () => request('/api/call/token', { method: 'POST' }),

  start: ({ sourceLanguage, targetLanguage, phoneNumber }) =>
    request('/api/call/start', {
      method: 'POST',
      body: {
        source_language: sourceLanguage,
        target_language: targetLanguage,
        phone_number: phoneNumber || null,
      },
    }),

  session: (sessionId) => request(`/api/call/${sessionId}`),

  end: (sessionId) => request(`/api/call/end/${sessionId}`, { method: 'POST' }),

  /** Server-Sent Events stream of live captions for one side of the call. */
  captionsUrl: (sessionId, viewer = 'A') =>
    `${BASE}/api/call/${encodeURIComponent(sessionId)}/captions?viewer=${viewer}`,
}
