# listen

Songs you replay become a playlist. Songs you discover become a monthly archive.

Runs at `listen.coleopteras.org`. FastAPI + SQLite + vanilla JS on [Pico CSS](https://picocss.com),
one container, no build step.

## What it does

- **Favourites.** Counts plays per track. Once a track crosses your threshold it's added to a
  playlist (created on first use, reused thereafter).
- **Discovery archive.** Tracks you play out of Discover Weekly get filed into a
  `January '26 Discovery` playlist for that month.
- **Multiple accounts.** Everyone logs in with their own Spotify account and gets their own
  counts, settings and playlists.

## How play counting works

Counts come from a cursored sweep of `GET /me/player/recently-played`, not from watching
playback progress. Spotify keeps the last 50 plays server-side and stamps each with a stable
`played_at`, which buys two things:

- **Nothing is missed.** Plays that happened while this container was down land on the next
  sweep. A progress-polling loop can only see what it was awake for.
- **Nothing is counted twice.** `UNIQUE(user_id, track_id, played_at)` makes re-reading an
  overlapping window a no-op, so the sweep deliberately rewinds a few seconds each time rather
  than risking a play falling through the gap.

`GET /me/player` is polled too, but only for the live now-playing panel and for discovery
capture. It never increments a count, so the two can't double up.

One consequence: Spotify decides what counts as a play (roughly 30 seconds in) and never reports
how far through a track you got, so `min_completion_ratio` **cannot** gate favourite counting.
It applies to discovery capture, which does see live progress.

## Why the Discovery archive works the way it does

You can't read Discover Weekly through the API. `GET /playlists/{id}/items` is owner- or
collaborator-only and 403s otherwise, and since 2024-11-27 algorithmic and Spotify-owned
playlists are off limits to any app that wasn't already in extended quota mode. There is no
workaround that enumerates the 30 tracks.

What *is* still visible is `context.uri` on the playback object, which names the playlist a track
is being played from even when that playlist's contents are unreadable. So the archive is built
from what you actually listen to. **A week you don't listen archives nothing** — that's the
accepted trade-off.

Since we can't read the playlist's *name* either, the app learns which playlist is your Discover
Weekly by observation: it logs playlist contexts it can't identify and shows you the track you
just heard from one, so you can label it in a single click. There's a paste-a-link field as a
fallback.

## Spotify setup

This runs in Development Mode, which since February 2026 means:

- The app owner needs **Spotify Premium**, or the app stops functioning.
- **One Client ID per developer**, and **five authorised users** per app. Each person's Spotify
  account email has to be added to the app's allowlist in the
  [dashboard](https://developer.spotify.com/dashboard).

Redirect URIs (Spotify rejects `localhost`; use `127.0.0.1` for loopback):

```
https://listen.coleopteras.org/api/auth/callback    # production
http://127.0.0.1:8090/api/auth/callback             # local development
```

Scopes requested: `user-read-recently-played`, `user-read-playback-state`,
`user-read-currently-playing`, `playlist-read-private`, `playlist-modify-public`,
`playlist-modify-private`.

## Check the API before trusting it

Development Mode endpoint availability is the biggest external unknown. Verify it against a real
account in one pass:

```bash
python scripts/probe_api.py
python scripts/probe_api.py https://open.spotify.com/playlist/<discover-weekly-id>
```

It reports the real status code for every endpoint the app depends on, creates and deletes one
throwaway playlist, and confirms that reading Discover Weekly is blocked.

## Running locally

```bash
cp .env.example .env          # fill in CLIENT_ID, CLIENT_SECRET, SESSION_SECRET
docker compose up --build     # http://127.0.0.1:8090
```

Or without Docker:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/ -q
.venv/bin/uvicorn app.main:app --reload --port 8090
```

## Deployment

The image publishes to `ghcr.io/nikkolaka/fav-songs-py:latest` on every push to `main`, after the
tests pass. On the homelab it runs as the `listen` service in `/opt/mediastack/docker-compose.yml`
behind Caddy, with secrets in `/opt/mediastack/.env` and the database bind-mounted to
`/opt/mediastack/listen/data`:

```bash
ssh root@pve
pct enter 100
cd /opt/mediastack
docker compose pull listen && docker compose up -d listen
```

The database lives on the container's own filesystem, deliberately **not** under `/mnt/media` —
that path is backed by the USB-attached ZFS pool, and a pool suspend would wedge this app along
with everything else holding a handle there.

## Layout

| Path | What's in it |
|---|---|
| `app/config.py` | Environment config, scopes, limits |
| `app/db.py` | SQLite schema and queries; refresh tokens encrypted with Fernet |
| `app/spotify.py` | OAuth, per-user token refresh, 429 backoff |
| `app/playlists.py` | Find-or-create by name, cached membership |
| `app/tracker.py` | The sweep, the live poll, one asyncio task per user |
| `app/discovery.py` | Month playlists, context matching |
| `app/main.py` | Routes and session cookies |
| `app/web/` | `index.html`, `app.js`, vendored `pico.min.css` |
| `scripts/probe_api.py` | Endpoint availability check |
| `tests/` | Sweep idempotency, downtime recovery, discovery capture |

**spotipy must stay at 2.26.0 or newer.** It's the first release targeting the February 2026
endpoint layout (`/playlists/{id}/items`, `POST /me/playlists`); 2.25.x calls paths Spotify has
removed.
