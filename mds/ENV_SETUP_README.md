# FavSongs Local-Only Deployment (CasaOS + Portainer)

FavSongs is now scoped to local-network Docker deployment only.

## Scope assumptions

- Service is hosted on your home server via Portainer.
- UI/API should be reachable from LAN devices only.
- Spotify developer access is capped to you + 5 additional users.

## Core environment variables

- `CLIENT_ID`, `CLIENT_SECRET`, `REDIRECT_URI`: Spotify OAuth app settings.
- `LOCAL_ONLY_MODE=true`: Enables request filtering to local/private IP ranges.
- `ALLOWED_NETWORKS`: Comma-separated CIDRs allowed to access UI/API.
- `MAX_CONNECTED_USERS=6`: Hard cap for connected Spotify accounts.
- `APP_PORT=8080`: Host port published by the container.

Default private ranges are already set in `.env.example`.

## Local run (dev machine)

1. Create env file:

```bash
cp .env.local.example .env.local
```

2. Fill Spotify credentials in `.env.local`.

3. Ensure Spotify redirect URI includes:

`http://127.0.0.1:8000/api/auth/spotify/callback`

4. Start:

```bash
docker compose -f compose.local.yml --env-file .env.local up --build
```

5. Open:

`http://127.0.0.1:8000`

## Portainer stack (CasaOS)

Use `compose.portainer.yml` and set stack env vars in Portainer:

- `CLIENT_ID`
- `CLIENT_SECRET`
- `REDIRECT_URI` (example: `http://192.168.1.25:8080/api/auth/spotify/callback`)
- Optional overrides: `APP_PORT`, `MAX_CONNECTED_USERS`, `ALLOWED_NETWORKS`

After deploy, open `http://<casaos-lan-ip>:<APP_PORT>` from LAN clients.

## Behavior notes

- Existing connected users can always sign back in.
- New Spotify users are blocked when `MAX_CONNECTED_USERS` is reached.
- Favorites tracking logic (listen frequency, thresholds, auto-add, etc.) remains unchanged.
