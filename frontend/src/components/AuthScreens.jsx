import { useState } from 'react'

import { api } from '../lib/api'
import { Link } from '../lib/router'
import { Alert, Button, Field, Input, PasswordInput } from './ui'

const MIN_PASSWORD = 12

/**
 * Sign in, and ask for access. One page for members and admins alike: the
 * server decides who is an admin, and where each lands afterwards follows.
 *
 * Same editorial split as the setup screen: the brand on the open
 * background, the form on the raised panel with its gold seam.
 */
function AuthLayout({ heading, lede, children }) {
  return (
    <div className="setup auth">
      <div className="setup__brand enter">
        <p className="eyebrow">Real-Time Interpretation</p>
        <h1 className="display">{heading}</h1>
        <p className="lede">{lede}</p>
        <p className="setup__mark">Interpreter · R&amp;D preview</p>
      </div>
      <div className="setup__form enter">
        <div className="setup__inner">{children}</div>
      </div>
    </div>
  )
}

export function LoginScreen({ onSignedIn }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)   // {tone, text}

  const submit = async (event) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setProblem(null)
    try {
      onSignedIn(await api.login(email, password))
    } catch (error) {
      // Pending and turned-off accounts are told why; the server only says
      // so once the password was right.
      const waiting = error.code === 'PENDING_APPROVAL'
      setProblem({ tone: waiting ? 'info' : 'error', text: error.message })
      setBusy(false)
    }
  }

  return (
    <AuthLayout
      heading={<>Welcome <em>back</em>.</>}
      lede="Sign in to place translated calls and read back every conversation."
    >
      <form onSubmit={submit} noValidate>
        <h2 className="auth__title">Sign in</h2>
        <Alert tone={problem?.tone}>{problem?.text}</Alert>

        <Field label="Email" htmlFor="login-email">
          <Input
            id="login-email"
            type="email"
            name="email"
            autoComplete="username"
            inputMode="email"
            required
            value={email}
            disabled={busy}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field label="Password" htmlFor="login-password">
          <PasswordInput
            id="login-password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            disabled={busy}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>

        <div className="auth__submit">
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>
        </div>

        <p className="auth__switch">
          New here? <Link to="/request-access">Request access</Link>
        </p>
        <p className="footnote">
          Forgot your password? Ask your administrator to reset it.
        </p>
      </form>
    </AuthLayout>
  )
}

export function RequestAccessScreen() {
  const [form, setForm] = useState({ name: '', email: '', password: '' })
  const [touched, setTouched] = useState({})
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)
  const [sent, setSent] = useState(null)

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))
  const blur = (key) => () => setTouched((t) => ({ ...t, [key]: true }))

  const errors = {
    name: form.name.trim().length < 2 ? 'Enter your name.' : null,
    email: /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email.trim())
      ? null : 'Enter a valid email address.',
    password: form.password.length < MIN_PASSWORD
      ? `Use at least ${MIN_PASSWORD} characters.` : null,
  }
  const shown = (key) => (touched[key] ? errors[key] : null)
  const valid = !errors.name && !errors.email && !errors.password

  const submit = async (event) => {
    event.preventDefault()
    setTouched({ name: true, email: true, password: true })
    if (!valid || busy) return
    setBusy(true)
    setProblem(null)
    try {
      const reply = await api.requestAccess(form)
      setSent(reply?.message ?? 'Request received.')
    } catch (error) {
      setProblem(error.message)
    } finally {
      setBusy(false)
    }
  }

  if (sent) {
    return (
      <AuthLayout
        heading={<>Request <em>received</em>.</>}
        lede="An administrator reviews every request. Once yours is approved, sign in with the email and password you just chose."
      >
        <div className="auth__done" role="status">
          <span className="auth__seal" aria-hidden="true">✓</span>
          <h2 className="auth__title">You're on the list</h2>
          <p className="lede">{sent}</p>
          <p className="auth__switch">
            <Link to="/login">Back to sign in</Link>
          </p>
        </div>
      </AuthLayout>
    )
  }

  return (
    <AuthLayout
      heading={<>Ask for <em>access</em>.</>}
      lede="Tell us who you are. An administrator reviews each request; once it is approved, you sign in with the password you choose here."
    >
      <form onSubmit={submit} noValidate>
        <h2 className="auth__title">Request access</h2>
        <Alert>{problem}</Alert>

        <Field label="Full name" htmlFor="req-name" error={shown('name')}>
          <Input
            id="req-name"
            name="name"
            autoComplete="name"
            maxLength={100}
            value={form.name}
            disabled={busy}
            invalid={Boolean(shown('name'))}
            onChange={set('name')}
            onBlur={blur('name')}
          />
        </Field>
        <Field label="Work email" htmlFor="req-email" error={shown('email')}>
          <Input
            id="req-email"
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            value={form.email}
            disabled={busy}
            invalid={Boolean(shown('email'))}
            onChange={set('email')}
            onBlur={blur('email')}
          />
        </Field>
        <Field
          label="Choose a password"
          htmlFor="req-password"
          error={shown('password')}
          hint={`At least ${MIN_PASSWORD} characters. A short phrase is easiest to remember.`}
        >
          <PasswordInput
            id="req-password"
            name="new-password"
            autoComplete="new-password"
            maxLength={128}
            value={form.password}
            disabled={busy}
            invalid={Boolean(shown('password'))}
            onChange={set('password')}
            onBlur={blur('password')}
          />
        </Field>

        <div className="auth__submit">
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Sending…' : 'Request access'}
          </Button>
        </div>

        <p className="auth__switch">
          Already approved? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </AuthLayout>
  )
}
