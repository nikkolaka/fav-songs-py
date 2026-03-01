# Multi-Account UI Proposal

This document outlines a UI approach to support multiple Spotify accounts using this application.

## Goals
- Allow each user to connect their own Spotify account via OAuth.
- Keep per-user play history and favorite thresholds isolated.
- Support concurrent users without corrupting data or blocking each other.

## High-Level Architecture
- **Frontend:** Single-page web UI (React/Vue/Svelte) served by a lightweight backend.
- **Backend API:** Python (FastAPI/Flask) service that manages OAuth, scheduling, and persistence.
- **Worker loop:** Per-user background task that polls playback and updates state.
- **Storage:** SQLite for single-host deployments; Postgres for multi-host scaling.

## Core UI Screens
- **Login / Connect Spotify**
  - “Connect Spotify” button that starts OAuth flow.
  - Shows required permissions and lets the user proceed.
- **Dashboard**
  - Current track, progress bar, last played time.
  - Current play count vs favorite threshold.
  - Toggle to pause/resume tracking.
- **Favorites**
  - List of tracks with play counts and last played time.
  - “Force add to playlist” action (optional).
- **Settings**
  - Favorite threshold, completion ratio, check interval.
  - Playlist name and privacy setting.

## OAuth Flow (Multi-User)
1. User clicks “Connect Spotify.”
2. Backend creates a state token and redirects to Spotify auth.
3. Spotify redirects to `/callback` with code + state.
4. Backend exchanges code for tokens and stores them by `user_id`.
5. Backend starts (or schedules) a tracker loop for that user.

## Data Model (Multi-User)
- `users`
  - `id`, `spotify_user_id`, `display_name`, `created_at`
- `tokens`
  - `user_id`, `access_token`, `refresh_token`, `expires_at`
- `tracks`
  - `id`, `track_id`, `name`, `artist`
- `plays`
  - `user_id`, `track_id`, `play_instance_id`, `played_at`, `completed`
- `favorites`
  - `user_id`, `track_id`, `occurrences`, `last_played`

## API Endpoints (Example)
- `POST /auth/spotify/start`
- `GET /auth/spotify/callback`
- `GET /me`
- `GET /me/now-playing`
- `GET /me/favorites`
- `POST /me/settings`
- `POST /me/tracker/start`
- `POST /me/tracker/stop`

## Concurrency & Scheduling
- Use a per-user background task scheduler (Celery, APScheduler, or native asyncio tasks).
- Ensure a single tracker instance per user to avoid double counting.
- Store the last processed `play_instance_id` per user to prevent duplicates.

## Security Considerations
- Encrypt refresh tokens at rest.
- Use short-lived access tokens and refresh automatically.
- Protect API with session cookies or JWTs.
- Validate OAuth state tokens to prevent CSRF.

## Deployment Path
- Start with SQLite and a single backend container for simplicity.
- Move to Postgres and a worker container for scale.
- Add a reverse proxy (Caddy/Nginx) for HTTPS and static frontend hosting.
