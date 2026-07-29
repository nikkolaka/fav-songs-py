# listen

Songs you actually listen to become a playlist. Songs you discover become a monthly archive.

Runs at `listen.coleopteras.org`. FastAPI + SQLite + vanilla JS on [Pico CSS](https://picocss.com),
one container, no build step.

## What it does

- **Favourites.** Counts the plays where you heard enough of the track. Once a track crosses
  your threshold it's added to a playlist (created on first use, reused thereafter).
- **History.** Every play is kept, forever, with when you played it and how much of it you
  heard — searchable and filterable.
- **Discovery archive.** Every track from your Discover Weekly gets filed into that
  month's playlist — `July's Discover`, `August's Discover` — with AI-generated music
  filtered out.
- **Multiple accounts.** Everyone logs in with their own Spotify account and gets their own
  history, settings and playlists.

## How listening is measured

Spotify's history (`GET /me/player/recently-played`) tells you a track was played and nothing
else — it decides what counts as a play on its own terms, roughly 30 seconds in, and never
reports how far through you got. Skipping a track after 30 seconds and loving it all the way
through look identical there.

So completion is measured instead, from `GET /me/player`, the one endpoint that reports
`progress_ms`. A **session** is one continuous playback of one track: it opens when the track
first appears in a poll, absorbs each poll that still looks like the same playback, and closes
when the track changes or playback stops. What it accumulates is *audio heard* — see
`app/listens.py`:

- **Time, not position.** Each poll credits at most the wall-clock time that actually elapsed,
  so seeking to the last ten seconds doesn't make a track 97% listened.
- **The unobserved tail.** With a 30-second interval, the last poll of a three-minute track sits
  around 2:50, which would cap every complete listen at ~94%. When a session closes we know when
  the next track started, so the remaining stretch is credited up to the track's own duration and
  a full play reaches 1.0.
- **Pauses don't count.** A track paused and abandoned gets no credit for the time it sat there.

`min_completion_ratio` is the threshold that measurement is compared against, and it now gates
**both** favourites and the discovery archive — one setting, one meaning of "listened to it".

### What the sweep is still for

The cursored sweep of recently-played still runs, but only to backfill the history with plays the
poll never saw — the ones from while the container was down. Those are recorded and marked
**unverified**: they have no measured completion, so they can never promote anything. That is the
deliberate trade. A play that happened while this app was offline cannot count towards a
favourite, because there is no honest way to say whether it was heard or skipped.

Rows are reconciled rather than duplicated. A live-measured listen and Spotify's own entry for the
same play are matched on track and time (Spotify never documents whether `played_at` marks the
start or the end of a play, so the window covers both), and a partial unique index on
`(user_id, track_id, history_played_at)` keeps re-reading an overlapping window a no-op.

## The history

`listens` is the one table meant to grow without bound, so nothing reads it with a scan:

- **Keyset pagination.** The cursor is the last row's `(played_at, id)`, not an `OFFSET`, so page
  500 costs what page 1 costs. `EXPLAIN QUERY PLAN` is asserted in the tests to stay an index
  seek with no sort.
- **FTS5 for search.** An external-content index over track and artist with `prefix='2 3 4'`, so
  `rad` finds Radiohead without a leading-wildcard scan, and `sigur ros` finds `Sigur Rós`.
  Triggers keep it in step with the table; if a SQLite build lacks FTS5 it degrades to `LIKE`
  rather than failing to start.
- **Nothing held client-side.** Filters run on the server, pages are appended, and a superseded
  keystroke's response is discarded rather than rendered.

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

### Playlist privacy is not ours to control

The `public` flag is passed on create, but Spotify largely ignores it — a playlist created
with `public: false` reads back as `public: true`, confirmed on this account. Even when it
does stick, it only governs whether the playlist is listed on your profile and in search;
it has never controlled link access, so anyone with the URL can open the playlist either
way. Set playlist privacy in the Spotify client if it matters.

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
| `app/listens.py` | Session accounting — how much of a track was actually heard |
| `app/spotify.py` | OAuth, per-user token refresh, 429 backoff |
| `app/playlists.py` | Find-or-create by name, cached membership |
| `app/tracker.py` | The live poll, the backfill sweep, one asyncio task per user |
| `app/discovery.py` | Embed read, month playlists, context matching |
| `app/aiblocklist.py` | Live AI-artist blocklist, cached with fallback |
| `app/main.py` | Routes and session cookies |
| `app/web/` | `index.html`, `app.js`, vendored `pico.min.css` |
| `scripts/probe_api.py` | Endpoint availability check |
| `tests/` | Completion measurement, sweep idempotency, history paging, discovery |

## Upgrading from the play-counting version

The schema migrates itself on first start. `plays` (a 30-day dedup ledger) folds into `listens`
(the permanent history) as `legacy` rows — recorded, unverified, never counted — and
`play_counts.occurrences` becomes `qualified_plays` with its tally carried over as-is. Nobody's
progress towards a playlist resets, and nothing already promoted gets removed. From then on only
measured listens move the counter.

**spotipy must stay at 2.26.0 or newer.** It's the first release targeting the February 2026
endpoint layout (`/playlists/{id}/items`, `POST /me/playlists`); 2.25.x calls paths Spotify has
removed.
