# FavSongs Public Explorer Environments

This app now runs in **read-only public lookup mode**.

It no longer performs:
- Spotify OAuth user login.
- Playback tracking.
- Playlist creation or playlist modification.
- Local database/session/token persistence.

## Required Environment Variables

- `CLIENT_ID`
- `CLIENT_SECRET`

These are used for Spotify **Client Credentials** authentication (application-level, no user login).

## Optional Tuning Variables

- `SPOTIFY_HTTP_TIMEOUT_SECONDS` (default `20`)
- `TOKEN_SKEW_SECONDS` (default `60`)
- `MAX_LOOKUP_CANDIDATES` (default `12`)
- `MAX_PUBLIC_PLAYLISTS` (default `200`)
- `MAX_LIKED_PLAYLISTS` (default `4`)
- `MAX_TRACKS_PER_LIKED_PLAYLIST` (default `75`)
- `DEFAULT_PLAYLIST_TRACK_LIMIT` (default `100`)
- `LIKED_PLAYLIST_KEYWORDS` (default `liked,favorite,favourites,favorites,saved,heart,hearts`)

## Local Run

1. Create local env file:

```bash
cp .env.local.example .env.local
```

2. Fill in `CLIENT_ID` and `CLIENT_SECRET` in `.env.local`.

3. Start local stack:

```bash
docker compose -f compose.local.yml up --build
```

4. Open:

`http://127.0.0.1:8000`

5. Stop:

```bash
docker compose -f compose.local.yml down
```

## Portainer + Cloudflare

Set these in the stack:
- `CLIENT_ID`
- `CLIENT_SECRET`
- Optional tuning variables above
- `CLOUDFLARED_TOKEN` (for the cloudflared sidecar)

## API Limitation Notes

Spotify public API does **not** expose another user's:
- Private Liked Songs
- Following graph (artists/users)

The UI shows these sections with explicit limitation notices and optional inferred data from public playlists.
