# Hosting at https://voice.softsuaveai.space (Cloudflare Tunnel)

One public address carries the whole app. The backend on `127.0.0.1:8000`
serves the built web page at `/`, the API at `/api` and `/health`, and Twilio's
media WebSocket at `/ws`. The tunnel exposes only that port; nothing is opened
on the router or firewall.

```
browser / Twilio ──https──> Cloudflare ──tunnel──> cloudflared (this PC) ──> 127.0.0.1:8000
```

## One-time setup

1. Install cloudflared: `winget install --id Cloudflare.cloudflared`
   (or the portable `cloudflared.exe` on your PATH).
2. Log in. A browser opens; pick the `softsuaveai.space` zone and authorise:
   `cloudflared tunnel login`
3. Create the tunnel and its DNS record:
   ```
   cloudflared tunnel create voice-translation
   cloudflared tunnel route dns voice-translation voice.softsuaveai.space
   ```
   Credentials land in `%USERPROFILE%\.cloudflared\`. Never commit them.
4. `backend/.env`:
   ```
   BACKEND_PUBLIC_URL=https://voice.softsuaveai.space
   FRONTEND_URL=https://voice.softsuaveai.space
   ```
5. Twilio Console → Voice → TwiML Apps → your app → Voice Request URL:
   `https://voice.softsuaveai.space/api/call/twiml/A` (POST).

## Every time

Stop any backend already on port 8000, then from the repo root:

```
powershell -ExecutionPolicy Bypass -File deploy\start.ps1
```

It builds the page, starts the backend in a new window and runs the tunnel.
Open https://voice.softsuaveai.space from any computer.

## Local development

Unchanged: backend on :8000, `npm run dev` on :5173. With `BACKEND_PUBLIC_URL`
pointing at the tunnel, Twilio still needs the tunnel running to reach you.

## Accounts

Nobody can place a call without signing in. People ask for access on the
sign-in page; an admin approves them under **Admin → Requests**. Members see
their own call history; admins see every member and every call.

One-time, on the host:

1. `backend/.env`: `ADMIN_EMAIL=<owner email>` (see `backend/.env.example`).
2. Set the owner's password. It is typed at a hidden prompt, never in a page:
   ```
   cd backend
   ..\.venv\Scripts\python.exe -m app.cli set-password <owner email>
   ```
   The same command resets anyone's forgotten password and signs them out.

Everything is kept in one SQLite file, `backend/data/app.db` (created on first
start, never committed). Back it up by copying it. Moving to another computer:
copy `backend/.env` **and** `backend/data/app.db` along with the tunnel
credentials.

## Before sharing the link

Sign-in keeps strangers from placing calls. Cloudflare Access (Zero Trust →
Access → Applications → Self-hosted) on `voice.softsuaveai.space` is still a
good outer lock: allow only your team's emails, with a Bypass policy for the
paths Twilio calls: `/api/call/twiml/*`, `/api/call/status` and
`/ws/media-stream`.
