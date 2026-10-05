# Real-Time Voice Translation

Two-way live speech translation between a **browser user** and an **ordinary mobile phone**.
User 1 speaks Tamil, User 2 hears Hindi; User 2 replies in Hindi, User 1 hears Tamil.
User 2 installs nothing — they answer a normal phone call.

Full architecture, research and decisions: **[`docs/VOICE_TRANSLATION_DESIGN.md`](docs/VOICE_TRANSLATION_DESIGN.md)**

## How it works

Two independent Twilio call legs, each connected to our backend by its own
bidirectional Media Stream. Twilio never bridges them — **the backend is the
bridge**. Audio arriving on one leg is translated and played into the *other*
leg only.

```
Browser ──┐                                    ┌── Indian mobile
          │ Leg A                       Leg B  │
          └──► Twilio ◄──┐          ┌──► Twilio ◄──┘
                         │          │
                    FastAPI (the bridge)
                         │          │
                   OpenAI A→B   OpenAI B→A
```

## Status

| Phase | State |
|---|---|
| 1. Research + architecture | done |
| 2. Backend foundation | **done** — session registry, routing rule, Media Stream WebSocket |
| 3. React UI | **done** — setup screen, live call screen, real call states |
| 4–5. Twilio token + call legs | endpoints done, not exercised against live Twilio |
| 6. Media Streams | done (audio routes leg-to-leg) |
| 7. OpenAI translation | **done** — two selectable backends, see below |
| 8–9. Two-way routing | translator output already routes to the peer; needs a second leg to verify |
| 10. Barge-in | **done** — our own energy VAD, identical across both backends |

## Prerequisites

- Python 3.11+
- A Twilio account. **Trial is enough for the browser-only pipeline** — Media
  Streams and translation were verified working on a trial account. To dial a
  real phone you must either verify that number (Phone Numbers -> Manage ->
  Verified Caller IDs) or upgrade; trial rejects unverified destinations with
  error 21219.
- A Twilio account with a **non-Indian** voice number (India cannot be called from an Indian number)
- India enabled in **Voice → Settings → Geo Permissions**
- `ngrok` or another HTTPS tunnel — Twilio cannot reach `localhost`
- An OpenAI API key (only needed from phase 7)

## Setup

```bash
cd backend
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill it in
```

Start the tunnel and put its URL in `BACKEND_PUBLIC_URL`:

```bash
ngrok http 8000
```

In the Twilio Console, set your **TwiML App's Voice URL** to:

```
<BACKEND_PUBLIC_URL>/api/call/twiml/A
```

Both must be updated every time ngrok restarts — that is the cause of most
"it worked yesterday" failures.

## Run

Three terminals:

```bash
# 1 — tunnel (Twilio cannot reach localhost)
ngrok http 8000

# 2 — backend
cd backend
uvicorn app.main:app --reload --port 8000

# 3 — frontend
cd frontend
npm install        # first time only
cp .env.example .env
npm run dev        # http://localhost:5173
```

Check the backend is up: `curl http://localhost:8000/health`

## Test

```bash
cd backend
python -m pytest -q
```

The suite covers the µ-law codec, input validation, log redaction, and — most
importantly — that audio routes to the opposite participant, never back to its
source, and never leaks between concurrent calls.

### Echo test (proves the whole Twilio path)

Set `ECHO_MODE=true` in `backend/.env`, restart the backend, then press
**Start translated call** in the browser. Your own voice comes back to you.
**Use headphones** — on speakers it will feed back.

That single test confirms the access token, the TwiML App, the tunnel, the
Media Stream WebSocket and bidirectional audio injection all at once.

## Environment variables

| Variable | Purpose |
|---|---|
| `TWILIO_ACCOUNT_SID` | Account identifier |
| `TWILIO_AUTH_TOKEN` | Webhook signature validation **only** |
| `TWILIO_API_KEY` / `TWILIO_API_SECRET` | Signing browser Access Tokens |
| `TWILIO_TWIML_APP_SID` | TwiML App backing the browser leg |
| `TWILIO_PHONE_NUMBER` | Caller ID for the PSTN leg — must be non-Indian |
| `TRANSLATION_BACKEND` | `translate` (default), `realtime` or `gemini` — which backend to use. `OPENAI_TRANSLATE_MODE` is the old name and still works |
| `OPENAI_API_KEY` | Translation on the two OpenAI backends |
| `OPENAI_TRANSLATE_MODEL` / `OPENAI_REALTIME_MODEL` | Model id for each OpenAI backend |
| `GEMINI_API_KEY` / `GEMINI_TRANSLATE_MODEL` | Translation on the `gemini` backend |
| `BACKEND_PUBLIC_URL` | Public HTTPS tunnel; `wss://` stream URL is derived from it |
| `FRONTEND_URL` | CORS origin |
| `DEMO_MODE` | `true` = browser↔browser, no PSTN call, no telephony spend |
| `ECHO_MODE` | Milestone 2 only: route audio back to its sender. Use headphones |
| `VALIDATE_TWILIO_SIGNATURE` | Leave `true` outside local tests |

No secret is ever sent to the browser. The frontend receives only a
short-lived Access Token.

## Translation backends

Three are supported; switch with `TRANSLATION_BACKEND` and a restart.

| | `translate` (default) | `realtime` | `gemini` |
|---|---|---|---|
| Model | `gpt-realtime-translate` | `gpt-realtime-2.1` | `gemini-3.5-live-translate-preview` |
| Endpoint | `/v1/realtime/translations` | `/v1/realtime` | Gemini Live API |
| Latency | starts translating **mid-sentence** | waits for end of speech + VAD window | starts translating **mid-sentence** |
| Audio | 24 kHz PCM16 (we resample) | µ-law 8 kHz, no conversion | 16 kHz in / 24 kHz out (we resample) |
| Billing | $0.034 per audio-minute | per token | $0.0053/min in + $0.0315/min out; free tier available |
| Languages | 20; 14 as targets | 20, both ways | **76, both ways** |
| Tamil output | works, but **not documented** by OpenAI | officially supported | documented |

Gemini's free tier lets Google use call content to improve its products —
including the audio of the person being called. Use a paid-tier key for real
calls. `python scripts/probe_gemini_translate.py` checks a key and a language
pair end to end before you place a call.

`translate` is the default because latency is the question this POC exists to
answer. If its undocumented Tamil support ever regresses, set
`OPENAI_TRANSLATE_MODE=realtime` and restart — both backends are fully tested.

Compare them on a real call by watching `translation_latency` in the logs.

## Demo mode vs PSTN

`DEMO_MODE=true` uses two browser legs. The topology is identical to the real
thing, so the whole translation pipeline can be built and tested for a fraction
of a cent per minute and with no Indian carrier involved. Switch to
`DEMO_MODE=false` only when you are ready to dial a real phone.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Twilio error **21215** | India not enabled in Geo Permissions |
| Call fails on a trial account | Destination number not verified |
| Recipient hears a Twilio message first | Trial account greeting — upgrade to remove |
| `trial accounts have limited parameter access` | Trial restricts `calls.create`. Upgrade |
| `21219` unverified number | Trial: verify the destination, or upgrade |
| WebSocket never connects | `BACKEND_PUBLIC_URL` stale after an ngrok restart, or not `https` |
| `403 INVALID_SIGNATURE` | Public URL mismatch between Twilio's config and `BACKEND_PUBLIC_URL` |
| Audio arrives but nothing plays | Peer leg not connected yet — check `dropped_no_peer` in `/api/diagnostics/sessions` |
