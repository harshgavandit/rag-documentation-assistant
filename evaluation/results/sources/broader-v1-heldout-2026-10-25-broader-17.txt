# Apna Video Call

A full-stack video meeting app with peer-to-peer WebRTC media, shared PostgreSQL-backed signaling and room state, and guest or authenticated entry.

[Open the app](https://zoom-clone-blue-pi.vercel.app/) · [Meeting server](server/app.js) · [Media lifecycle](frontend/src/useMeeting.js) · [Integration tests](tests/integration.test.js)

## Features

- Start or join a meeting as a guest, or create an account and sign in.
- Invite others with a meeting link or room code.
- Make video and audio calls, mute your microphone, and turn your camera on or off.
- Share your screen and send messages in the meeting chat.
- Draw together on a shared whiteboard.
- Raise your hand, send emoji reactions, and see who is in the meeting.
- Record a selected screen or tab and download the video to your device.
- View your meeting history when signed in.

Meetings support up to **six people**. Anyone with the meeting link can join; rooms do not have an approval lobby or password.

## Architecture

```text
React / Vite browser clients
  ├─ Camera, microphone and screen → WebRTC peer connections
  ├─ Sign-in and history → Express HTTP API
  └─ Signaling, chat, reactions and whiteboard → Socket.IO
                                                 ↓
                                  PostgreSQL Socket.IO adapter
                                  Sessions, messages and presence
```

Media travels between peers, rather than through an application media server. The server coordinates room membership and SDP/ICE exchange. PostgreSQL shares signaling between server instances and persists accounts, rotating sessions, meeting history, chat and expiring participant leases.

| Engineering decision | Why it is there |
|---|---|
| Transactional room admission | A PostgreSQL advisory lock serializes joins to the same room before enforcing the six-participant cap. |
| One room per socket | Admission is reserved before the first asynchronous operation. Overlapping join requests cannot subscribe one socket to multiple rooms; failed database commits or adapter joins clear room privileges and subscriptions. |
| Expiring participant leases | Reconnect replaces the tab's previous lease; heartbeats refresh presence and stale leases expire after an interrupted process. |
| Room-scoped signaling | The server checks that signal recipients belong to the sender's room and bounds message sizes and event rates. |
| Hashed, rotating sessions | Access and refresh tokens are stored as hashes. Refresh replaces both tokens; logout revokes the stored session. |
| Authentication-rate identity | Direct deployments use the connection's peer address, ignoring client-supplied forwarding headers. Vercel deployments accept only a valid IP from the platform's `x-vercel-forwarded-for` header. |
| Explicit media cleanup | Peer connections and media tracks are stopped on exit; screen replacement and reconnect negotiation live in the shared meeting hook. |

The active implementation is [`server/`](server/) and [`frontend/`](frontend/). [`backend/`](backend/) contains the earlier implementation and is not the root application's runtime.

## Stack

React, Vite, Material UI, WebRTC, MediaRecorder, Express, Socket.IO, PostgreSQL, bcrypt, Node's test runner and Vitest.

## Run locally

Use **Node.js 22.x** and a dedicated PostgreSQL database. The PostgreSQL connection must support `LISTEN/NOTIFY`; use a direct connection rather than a transaction pooler.

```powershell
git clone https://github.com/harshgavandit/Zoom_Clone.git
cd Zoom_Clone
npm ci
npm ci --prefix frontend
Copy-Item .env.example .env.local
```

Set `DATABASE_URL` and, when using a pooled provider URL, `DATABASE_URL_UNPOOLED` in `.env.local`. The application prefers the unpooled URL. Schema tables are created on first readiness, so use an empty development database. `PORT` defaults to `8000`.

```powershell
# Terminal 1: HTTP API and signaling
npm start

# Terminal 2: Vite UI, with /api proxied to localhost:8000
npm --prefix frontend start
```

Open Vite's displayed local URL. For a single-origin production-style local run:

```powershell
npm run build
npm start
```

The server serves the built frontend at `http://localhost:8000`.

## Verification

```powershell
npm run check
npm --prefix frontend test
npm test
npm run build
```

`npm test` launches two local server instances against the configured PostgreSQL database and exercises authentication, refresh replay, logout revocation, room events and cross-room isolation. It creates synthetic accounts and meeting records; use a separate test database. Test ports `8121` and `8122` must be free.

The same command runs [admission-boundary regressions](tests/admission-boundaries.test.js) against the production connection handler with deterministic database/adapter failures, and [compressed-body checks](tests/compressed-body.test.js) against the installed HTTP middleware. These checks cover concurrent joins, failed-admission cleanup and spoofed forwarding headers. The two-instance suite verifies signaling and shared state; it does not measure real camera/audio quality or performance across different networks.

[CI](.github/workflows/ci.yml) supplies isolated PostgreSQL, runs frontend route tests and the two-instance integration suite, builds the UI and audits active dependencies. [`scripts/smoke.mjs`](scripts/smoke.mjs) contains the shared checks. `npm run test:live` defaults to `http://localhost:8000`; set `BASE_URL` and optionally `SECONDARY_URL` to test another deployment. It creates test accounts and meeting records, so use a dedicated environment and run it deliberately.

CI also checks the historical `backend/` dependencies separately with `npm ci --prefix backend --ignore-scripts`, `npm --prefix backend test`, and `npm audit --prefix backend --audit-level=moderate`. These offline checks exercise native bcrypt compatibility, Mongoose validation, existing authentication controllers and compressed-body parsing without connecting MongoDB or starting mediasoup workers. They do not replace a full SFU deployment test.

## Deployment and limits

[`vercel.json`](vercel.json) builds the Vite frontend and routes API/signaling traffic through [`api/index.js`](api/index.js). Configure the database URLs in the hosting environment. No database credential is sent to the browser.

- This is a small-group peer mesh, not an SFU. Browser bandwidth and CPU usage grow as more peers join.
- STUN is configured by default. Restricted networks may need an authenticated TURN relay via `TURN_URL`, `TURN_USERNAME` and `TURN_CREDENTIAL`; Vercel does not provide that relay.
- Camera and microphone access require browser permission and HTTPS, or localhost during development.
- Recordings stay on the user's device. The Recordings page lists downloads in the current browser; the files are saved in Downloads. There is no server-side recording archive.
- Guest rooms are accessible to anyone holding the link. This implementation does not claim enterprise meeting access controls or a production availability guarantee.

## Repository map

| Path | Contents |
|---|---|
| `server/` | Active Express API, PostgreSQL state and Socket.IO coordination |
| `frontend/src/` | React pages, meeting hook, media controls and UI |
| `api/` | Vercel entry point |
| `tests/`, `scripts/smoke.mjs` | Two-instance API and signaling checks |
| `.github/workflows/` | PostgreSQL-backed CI |
