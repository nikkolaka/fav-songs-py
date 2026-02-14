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

### Fast Local Commands

Use the helper script:

```bash
./scripts/local.sh up
./scripts/local.sh logs
./scripts/local.sh status
./scripts/local.sh down
./scripts/local.sh rebuild
```

If `.env.local` does not exist, the script creates it from `.env.local.example` and stops so you can fill credentials.

## Portainer Cloudflare Tunnel Envs

In each stack, set:

- `DEFAULT_PLAYLIST_NAME=Favourite Songs - Whatsit`
- `PLAYLIST_NAME_SUFFIX=[PROD]` (or `[CERT]` in cert stack)
- `REDIRECT_URI` matching that stack hostname callback.

Use distinct callback hosts for each stack (for example prod + cert subdomains).

## Git Branch + Auto Deploy (master -> Portainer)

Use this flow to develop on a feature branch and auto-deploy when merged into `master`.

1. Create and switch to a local branch:

```bash
git checkout -b feature/<short-name>
```

2. Make changes and run local validation:

```bash
./scripts/local.sh rebuild
```

3. Commit and push:

```bash
git add .
git commit -m "Describe changes"
git push -u origin feature/<short-name>
```

4. Open a PR from `feature/<short-name>` into `master` and merge.

5. In Portainer stack settings:
- Set deployment mode to Git repository.
- Set branch to `master`.
- Keep `compose.portainer.yml` as compose path.

6. In Portainer stack settings, copy the stack webhook URL (or create one if missing).

7. Add GitHub repository secrets:
- `PORTAINER_WEBHOOK_URL`: full Portainer webhook URL

8. Add GitHub Actions workflow `.github/workflows/deploy-portainer.yml`:

```yaml
name: Deploy to Portainer

on:
  push:
    branches: [master]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger Portainer webhook
        run: curl -fsS -X POST "${{ secrets.PORTAINER_WEBHOOK_URL }}"
```

9. After merge to `master`, GitHub Actions calls the Portainer webhook and Portainer pulls/deploys the latest commit.
