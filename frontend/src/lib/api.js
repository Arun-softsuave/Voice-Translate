const BASE = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')

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

async function request(path, { method = 'GET', body } = {}) {
  let response
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
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

export const api = {
  health: () => request('/health'),

  token: (identity = 'user1') =>
    request('/api/call/token', { method: 'POST', body: { identity } }),

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
}
