# FavSongs Tracker: Long-Term Stability and Scaling Review

This document captures recommended changes to improve listening progress tracking, multi-user readiness, and Docker stability/security for long-running deployments.

## 1) Listening Progress Methodology (Logic Changes)

### Critical fixes
- **Count plays on track transition, not during the same track**. Persist the last known `progress_ms` and `duration_ms` for the current track, then evaluate completion *when a track changes or playback stops*. This avoids missing completed plays for short tracks and avoids double-counting during long tracks.
- **Fix the skip-window logic** so that a new track is not ignored for up to 30 seconds after a transition. Introduce a `last_processed_track_id` or reset the debounce timer when a new track starts.

### Make play counting deterministic
- **Use a per-play instance key** (e.g., `play_instance_id = timestamp - progress_ms`) and store it with the track to prevent counting the same play multiple times. This prevents the 5-minute window from counting a single long play more than once.
- **Treat pause/resume distinctly from track end**. A paused playback should not be logged as “SKIPPED.” Use `is_playing` and progress deltas to differentiate pause/resume vs end/track change.

### Data integrity improvements
- **Write `fav_songs.json` atomically** (write to temp file + rename) to avoid partial writes on crash.
- **Move `fav_songs.json` into a persistent volume path** (e.g., `/app/data/fav_songs.json`) so container restarts do not wipe history.
- **Validate required env vars at startup** and fail fast with a clear error if `CLIENT_ID`, `CLIENT_SECRET`, or `REDIRECT_URI` are missing.

### Optional robustness improvements
- **Use the Spotify “recently played” endpoint** as a fallback or for reconciliation, especially if polling misses transitions.
- **Make thresholds configurable** (e.g., `FAVORITE_THRESHOLD`, `MIN_COMPLETION_RATIO`, `CHECK_INTERVAL`) via env to support different user expectations without code changes.

## 2) Multi-User Readiness

- **Namespacing**: Use `user_id` as a key in the persistent store so multiple users can be tracked independently.
- **Storage**: Replace the single JSON file with a small SQLite DB (or Postgres/Redis if remote multi-instance) to handle concurrency and avoid corruption.
- **OAuth**: A multi-user setup requires a web callback route and per-user token storage; a single CLI flow won’t scale for multiple users in one container.

## 3) Docker Stability & Security

### Persistence and volumes
- **Persist both cache and favorites** in named volumes and ensure the code uses those paths.
- **Ensure write permissions** for the runtime user on mounted volumes (either an entrypoint `chown` step or Docker volume options for UID/GID).

### Hardening
- **Pin the base image** (e.g., `python:3.12-slim@sha256:...`) to reduce supply chain drift.
- **Drop unused dependencies** (e.g., `redis` if unused) to reduce attack surface.
- **Run with a read-only root filesystem** and mount only `/app/data` and `/tmp` as writable.
- **Add `no-new-privileges` and drop all Linux capabilities** in compose.

### Operational resilience
- **Add a healthcheck** that verifies the process is still running and can reach Spotify.
- **Backoff and retry** on API rate-limits (HTTP 429) using `Retry-After`.

## 4) Configuration Hygiene

- **Do not commit `.env`** files with real credentials; use a `.env.example` template instead.
- **Use `env_file` in compose** for local development, and secrets management for production.
- **Remove unnecessary `EXPOSE`** if the container does not serve HTTP.

## 5) Suggested Implementation Order

1. Move `fav_songs.json` into `/app/data` and update volume mapping.
2. Fix play counting on track transition with a `play_instance_id` guard.
3. Add env validation and atomic file writes.
4. Harden Docker image and compose (pinned base, least privilege, read-only FS).
5. Replace JSON with SQLite when multi-user support is required.
