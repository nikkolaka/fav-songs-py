# FavSongs Environments (PROD, CERT, LOCAL)

This project now supports environment-level playlist isolation so CERT and PROD do not touch the same Spotify playlist by accident.

## Isolation Rule

Set `PLAYLIST_NAME_SUFFIX` per environment:

- PROD: `[PROD]`
- CERT: `[CERT]`
- LOCAL: `[LOCAL]`

The app resolves/creates playlists using:

`<playlist_name> <PLAYLIST_NAME_SUFFIX>`

Example:

- Base setting: `Favourite Songs - Whatsit`
- PROD playlist: `Favourite Songs - Whatsit [PROD]`
- CERT playlist: `Favourite Songs - Whatsit [CERT]`

## Required Variables

- `DEFAULT_PLAYLIST_NAME` controls the default playlist base name for new accounts.
- `PLAYLIST_NAME_SUFFIX` controls environment separation.

Both are wired into `compose.yml` and `compose.portainer.yml`.

## Local Test Environment (UI + Spotify)

1. Copy local env template:

```bash
cp .env.local.example .env.local
```

2. Fill in Spotify credentials in `.env.local`.

3. In Spotify Developer Dashboard, add redirect URI:

`http://127.0.0.1:8000/api/auth/spotify/callback`

4. Start local stack:

```bash
docker compose -f compose.local.yml up --build
```

5. Open:

`http://127.0.0.1:8000`

6. Stop local stack:

```bash
docker compose -f compose.local.yml down
```

Note: local compose uses a Docker named volume (`favsongs_local_data`) to avoid host permission issues on SQLite files.

## Portainer Cloudflare Tunnel Envs

In each stack, set:

- `DEFAULT_PLAYLIST_NAME=Favourite Songs - Whatsit`
- `PLAYLIST_NAME_SUFFIX=[PROD]` (or `[CERT]` in cert stack)
- `REDIRECT_URI` matching that stack hostname callback.

Use distinct callback hosts for each stack (for example prod + cert subdomains).
