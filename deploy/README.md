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

## Automated menus and voicemail

Calls that reach a recorded menu ("press one for sales") need two things the
call screen now has:

* **Keypad** — a third button beside Mute, once the far end has answered. The
  two legs of a translated call are never joined inside Twilio, so a keypress
  is sent to the backend, which plays the tone down the other line. Keys
  pressed are saved with the transcript.
* **Read the menu** — inside the keypad panel. A menu is spoken straight
  through, so its spoken translation arrives sentence by sentence long after
  the menu stopped listening. This stops playing it and leaves the captions,
  which arrive at once. It also stops the menu's voice cutting off what you
  are saying into it.

Neither needs any Twilio configuration. `DETECT_MACHINES=true` additionally
asks Twilio whether a person or voicemail answered, which puts "Voicemail —
speak your message now" on the screen; it costs a small fee per call and is
occasionally wrong, so it is off by default.

Whether a far-end menu accepts our tones depends on the carrier, and can only
be confirmed by a real call. To test safely, point one of your own Twilio
numbers at a TwiML Bin containing a `<Gather input="dtmf">` that reads the
digit back, and call it from the app.

## The silence before the first translation

Nothing can be translated until enough of a sentence has been said, so the
person who answered hears a few seconds of nothing. Nobody tells them that,
and real calls have ended with *"Hello? Am I audible?"* and a hang-up. Two
things address it:

* **An answer tone in the browser.** Two short notes the moment the line is
  genuinely open, so the caller starts talking immediately instead of waiting.
  Always on; nothing to configure.
* **Waiting music for the person who answers** - `WAIT_MUSIC=true`. It starts
  when their stream opens and stops the instant your first translated sentence
  reaches them. After that the call is silent between turns, as it always was.

**Their own speaking does not stop the music** - only your translated voice
does. That is deliberate: if they say "hello?" while it plays, it carries on
underneath them until there is something real to hear, and that "hello?"
still reaches you as usual.

It plays **once per call**. `stream_closed` logs `music_s` per leg, which is
how to tell from the logs how much of it was actually heard.

### Choosing the music

Five styles ship. Audition them, then pick one:

    cd backend
    ..\.venv\Scripts\python.exe scripts/make_hold_music.py --options
    ..\.venv\Scripts\python.exe scripts/make_hold_music.py --use melody

`--options` writes playable copies to your Downloads folder. `--use` promotes
one to `wait.ulaw`, which is what a live call plays; restart the backend
after. Your own track works too: `--from-wav track.wav`, mono 16-bit at 8000
or 24000 Hz, looping cleanly. Keep it quiet - their handset microphone picks
it up and sends it to the translator along with their voice.

If the music file is missing, no music plays and calls are unaffected.

### Two things that were tried and removed

**A spoken `<Say>` before `<Connect><Stream>`.** It broke every call: the
stream could not open until the sentence finished, so setup went from 1.1 s to
5-9 s, the caller's voice was discarded throughout, and hanging up during it
meant the stream was never reached at all. Five of seven production calls
produced nothing. `tests/test_wait_music.py::test_twiml_is_untouched_by_the_music`
guards against it returning; the number to watch in the logs is
`twiml_to_stream_ms`, which should stay near 1100 ms.

**Music in every mid-call gap, on both sides.** Built, tested and removed: on
a live line it never once played. A phone call almost never satisfies "nobody
is speaking and nobody has anything queued to hear" for a whole second at a
time - background noise alone keeps a speech detector awake every few seconds.
The one-off music above is what actually works, and being a one-off is what
keeps it simple enough to reason about.

## Before sharing the link

Sign-in keeps strangers from placing calls. Cloudflare Access (Zero Trust →
Access → Applications → Self-hosted) on `voice.softsuaveai.space` is still a
good outer lock: allow only your team's emails, with a Bypass policy for the
paths Twilio calls: `/api/call/twiml/*`, `/api/call/status` and
`/ws/media-stream`.
