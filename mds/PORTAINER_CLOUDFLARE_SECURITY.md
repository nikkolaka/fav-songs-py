# FavSongs Deployment Guide (CasaOS + Portainer + Cloudflare)

This project runs as a multi-account Spotify web app with session auth and persistent SQLite data.

## What Was Implemented

- Multi-account backend API with SQLite persistence and per-account tracker loops.
- Spotify OAuth connect flow for each account.
- Dashboard, favorites, settings, and tracker start/stop from the frontend.
- Per-user Spotify OAuth session auth.
- Dockerized web service for Portainer/CasaOS deployment.

## 1) Deploy on CasaOS / Portainer

### Prerequisites

- A machine on your network running CasaOS + Portainer.
- Spotify Developer app with credentials.
- This repo available on that machine.

### Spotify App Setup

1. Go to Spotify Developer Dashboard and open your app settings.
2. Add your redirect URI exactly as:
   `http://<YOUR_LAN_IP_OR_HOST>:8000/api/auth/spotify/callback`
3. If you will expose this on a public domain, also add:
   `https://songs.example.com/api/auth/spotify/callback`
4. Save changes.

### Environment File

Create `.env` in project root from `.env.example`:

```bash
cp .env.example .env
```

Fill at minimum:

- `CLIENT_ID`
- `CLIENT_SECRET`
- `REDIRECT_URI`

### Portainer Stack Deployment

1. In Portainer, go to `Stacks` -> `Add stack`.
2. Paste the contents of `compose.yml` from this repo.
3. Set stack name (for example `favsongs`).
4. Ensure `.env` values are available to the stack (either env file mount or variables in Portainer UI).
5. Deploy stack.

The app should be available at:

`http://<YOUR_SERVER_IP>:8000`

## 2) Connect Spotify Accounts

1. Open the app.
2. Click `Connect Spotify`.
3. Approve Spotify permissions.
4. Repeat for each friend/account you want tracked.

Each connected account appears in the account selector and has isolated data/settings.

## 3) Recommended Cloudflare Publishing (Private Access)

Recommended approach: **Cloudflare Tunnel + Cloudflare Access**.
This avoids opening inbound router ports directly.

### Steps

1. Add your domain to Cloudflare.
2. Install `cloudflared` on the CasaOS host.
3. Authenticate `cloudflared` with your Cloudflare account.
4. Create a tunnel and route a hostname (for example `songs.example.com`) to `http://localhost:8000`.
5. In Cloudflare Zero Trust, create an Access application for that hostname.
6. Restrict access to trusted identities only (emails, Google/GitHub org, or one-time PIN by allowlist).
7. Enable HTTPS-only mode in Cloudflare.

With this setup, users pass Cloudflare Access first, then authorize Spotify in-app.

## 4) Security Checklist for Trusted-Only Access

- Keep Cloudflare Access enabled with identity/email allowlist.
- Prefer Cloudflare Tunnel instead of opening router ports.
- Keep container images updated regularly.
- Keep Spotify credentials only in `.env` (never commit secrets).
- Back up `/app/data/favsongs.db` regularly.
- Enforce HTTPS for any public hostname; do not use plain HTTP on the open internet.

## 5) Optional Hardening

- Add rate limiting in front of the app (Cloudflare WAF rules).
- Restrict by geography/IP at Cloudflare if all users are in known locations.
- Require Cloudflare Access for internet-exposed deployments.
- Restrict approved email identities in Cloudflare Access as your primary outer auth policy.

## 6) Notes About Current Auth Model

Current app auth model is Spotify OAuth + session cookie.

- Every user authorizes their own Spotify account.
- Session and settings persistence come from the app SQLite DB volume.
- If non-owner users get Spotify authorization permission errors, configure Spotify app access mode/user allowlist (or move app to production/extended access mode).

## Persistence Rules

- Do not remove Docker volumes during redeploy if you want to keep settings and OAuth sessions.
- Portainer redeploy is safe when reusing the same stack volume.
- Deleting stack volumes will force users to reconnect and recreate settings.
