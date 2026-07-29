# listen

Songs you replay become a playlist. Songs you discover become a monthly archive.

Runs at `listen.coleopteras.org`. FastAPI + SQLite + vanilla JS on [Pico CSS](https://picocss.com),
one container, no build step.

## What it does

- **Favourites.** Counts plays per track. Once a track crosses your threshold it's added to a
  playlist (created on first use, reused thereafter).
- **Discovery archive.** Every track from your Discover Weekly gets filed into that
  month's playlist — `July's Discover`, `August's Discover` — with AI-generated music
  filtered out.
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

You can't read Discover Weekly through the **Web API**. `GET /playlists/{id}/items` is owner- or
collaborator-only and 403s otherwise, and since 2024-11-27 algorithmic and Spotify-owned
playlists are off limits to any app that wasn't already in extended quota mode. Verified against
this account: it returns 404.

Spotify's own **public embed page** for that same playlist does serve it. That's the page that
renders when you share a playlist link into Slack or a blog post — no auth, no API terms, and it
carries the full 30-track list. So the archive reads that:

```
GET https://open.spotify.com/embed/playlist/<id>   →  __NEXT_DATA__  →  30 tracks + URIs
GET https://open.spotify.com/oembed?url=...        →  "Discover Weekly"   (playlist name)
```

Worth being clear-eyed about: `__NEXT_DATA__` is an undocumented internal structure and can
change shape without notice. So it's wrapped in `EmbedUnavailable`, and when it fails the app
**falls back to capturing what you actually play** from that playlist, via `context.uri` on the
playback object — which stays visible even when the playlist's contents don't. The failure is
recorded on the source row and shown in the UI, so a broken read is loud rather than silent.

oEmbed is the safer of the two (a published link-preview standard) and is used only to resolve
names, so a playlist shows up as "Discover Weekly" rather than a 22-character hash. Anything
titled *Discover Weekly* registers itself as a source; anything else waits for one click.

### AI music filtering

Tracks are checked against [CennoxX/spotify-ai-blocker](https://github.com/CennoxX/spotify-ai-blocker)
before anything is written. That list ships **Spotify artist IDs**, not just names, so matching is
exact — a real band that happens to share a name with an AI act is never blocked. Since the embed
gives artist names only, each new track costs one `GET /tracks/{id}` to resolve its artist IDs.

- The CSV updates most days, so it's fetched live (daily), cached on disk, and falls back to the
  last good copy when GitHub is unreachable.
- A suspiciously small download is rejected rather than allowed to replace a good cache.
- **It fails open**: if the list can't be loaded, tracks are archived rather than dropped.
  Silently losing music you wanted is worse than archiving one you didn't.
- Blocked tracks are kept in `discovery_blocked` and listed in the UI, so the filter is auditable
  and a blocked track doesn't cost an API lookup on every sweep for the rest of the month.

### One caveat on the playlist naming

`July's Discover` carries no year, so July 2027 will resolve to the same name as July 2026. The
month→playlist mapping is stored per month in `discovery_playlists`, so nothing breaks while that
row exists, but a rebuilt database would append next year's tracks to this year's playlist. If you
want that closed off, change one line in `app/discovery.py`:

```python
return f"{parsed.strftime('%B')}'s Discover"        # July's Discover
return f"{parsed.strftime('%B')} '{parsed:%y} Discover"   # July '26 Discover
```

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
| `app/discovery.py` | Embed read, month playlists, context matching |
| `app/aiblocklist.py` | Live AI-artist blocklist, cached with fallback |
| `app/main.py` | Routes and session cookies |
| `app/web/` | `index.html`, `app.js`, vendored `pico.min.css` |
| `scripts/probe_api.py` | Endpoint availability check |
| `tests/` | Sweep idempotency, downtime recovery, discovery capture |

**spotipy must stay at 2.26.0 or newer.** It's the first release targeting the February 2026
endpoint layout (`/playlists/{id}/items`, `POST /me/playlists`); 2.25.x calls paths Spotify has
removed.
