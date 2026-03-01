# Headless `spotify2media` Refactor and Integration Plan

This document describes how to refactor `resources/spotify2media.py` into a purely headless service and integrate it into this project when the only inputs are:

- A Spotify username/profile identifier
- User-accessible public tags/signals (for example: playlist name keywords)
- No external CSV export flow (for example: no Exportify dependency)

## 1) What changes from the current script

Current `spotify2media.py` is a Tkinter desktop workflow with GUI state, drag/drop CSV, and direct process control inside UI callbacks.

Headless target:

- No Tkinter/UI imports
- No CSV upload requirement
- Input comes from backend APIs already present in this project (`/api/public/lookup`, `/api/public/playlists/{id}/tracks`)
- Download/transcode logic runs in background jobs, not in request handlers
- Output is API-observable job status + files on a mounted media volume

## 2) Data source without Exportify

Use the existing `SpotifyPublicService` in `app.py` as the source of track candidates:

1. Resolve username/query to a selected public profile.
2. Collect candidate playlists:
   - Owned public playlists
   - Followed public playlists (optional)
   - Derived "liked-style" playlists using keyword matching
3. Pull playlist tracks from Spotify Web API.
4. Convert each track into a normalized download request.

Important constraint:

- Without user OAuth scopes, private "Liked Songs" are not accessible.
- Any "liked songs" dataset must remain inferred from public playlists/tags.

## 3) Refactor architecture (recommended)

Split `spotify2media.py` into pure modules. Keep the same download heuristics where useful.

Suggested structure:

```text
services/
  media_models.py          # dataclasses for TrackRequest, DownloadJob, JobResult
  media_sources.py         # build TrackRequest list from username + tags via SpotifyPublicService
  media_matcher.py         # query building, deep-search scoring, duration checks
  media_downloader.py      # yt-dlp + ffmpeg execution, retries, archive handling
  media_metadata.py        # mutagen tagging and optional artwork embedding
  media_jobs.py            # job queue orchestration + persistence
workers/
  media_worker.py          # long-running job processor loop
```

Core principle:

- UI concerns are deleted.
- Backend integration points are explicit interfaces.

## 4) Mapping old code to new modules

- `convert_playlist()` -> split into:
  - `media_sources.build_requests_from_user_query(...)`
  - `media_matcher.select_candidate(...)`
  - `media_downloader.download_track(...)`
  - `media_metadata.apply_tags(...)`
- `open_settings()` / Tk `BooleanVar` settings -> replace with:
  - environment config + per-job JSON options
- `status_label` / `progress` updates -> replace with:
  - persisted `jobs` + `job_items` rows in SQLite
- artwork methods (`rename_album_art`, `embed_all_artwork`) -> keep only if reliable with headless naming; otherwise remove in v1

## 5) API integration design

Add headless endpoints in `app.py` (or a router module):

- `POST /api/media/jobs`
  - Input: `query`, `include_followed`, `playlist_ids` (optional), `keywords` (optional), conversion options
  - Output: `job_id`
- `GET /api/media/jobs/{job_id}`
  - Output: status, counters, errors, output path
- `GET /api/media/jobs/{job_id}/items`
  - Output: per-track result list
- `POST /api/media/jobs/{job_id}/cancel`
  - Output: cancellation acknowledgement

Minimal SQLite tables:

- `media_jobs`: id, created_at, started_at, finished_at, status, query, options_json, output_dir, error
- `media_job_items`: id, job_id, track_id, title, artist, status, file_path, error, youtube_url, duration_s

## 6) Easiest implementation paths

## Path A: Fastest (single container, in-process worker)

Best for initial proof-of-value.

1. Add services modules.
2. Add a simple thread-based worker started on app startup.
3. Persist job rows in SQLite.
4. Run `yt-dlp`/`ffmpeg` in subprocess from that worker.

Pros:

- Lowest code and ops overhead.
- Works with current deployment model quickly.

Cons:

- Worker lifecycle tied to API process.
- Restarts can interrupt jobs.

## Path B: Recommended (separate worker container)

Best for production reliability.

1. Keep API service for job creation/status only.
2. Add `media-worker` service in compose, sharing:
   - SQLite volume
   - media output volume
3. Worker polls `media_jobs` for queued work.

Pros:

- Better isolation and fault tolerance.
- API remains responsive under heavy download load.

Cons:

- Slightly more deployment complexity.

## Path C: Smallest code (synchronous endpoint)

Only for very small batches/testing.

1. `POST /api/media/convert` performs end-to-end work inline.
2. Returns only when done.

Pros:

- Simplest implementation.

Cons:

- Bad timeout behavior.
- Hard to scale and monitor.

## 7) Docker and runtime changes required

Your current compose runs `read_only: true`; downloading media requires writable storage. Minimum changes:

1. Add a writable volume for output and job temp files (for example `/app/media`).
2. Ensure `yt-dlp` and `ffmpeg` are available in the runtime image.
3. Keep `/tmp` writable for transient files.

Example adjustments:

- Dockerfile:
  - install `ffmpeg`
  - install `yt-dlp` (pip or binary)
  - include `mutagen` in `requirements.txt`
- Compose:
  - mount `./data/media:/app/media`
  - if keeping strict read-only rootfs, mount explicit writable paths for DB and media

## 8) "Username + tags" request model (practical)

Use a request payload like:

```json
{
  "query": "spotify_username_or_profile_url",
  "keywords": ["liked", "favorites", "heart"],
  "include_followed_public_playlists": false,
  "max_playlists": 30,
  "max_tracks_per_playlist": 100,
  "transcode_mp3": false,
  "deep_search": true,
  "exclude_instrumentals": false
}
```

Selection strategy:

1. Resolve user via existing candidate logic.
2. Filter playlists by keyword tags and/or explicit playlist IDs.
3. De-duplicate tracks by Spotify track ID.
4. Build deterministic output names (`NNN - Artist - Title.ext`).

## 9) Suggested first milestone (lowest risk)

1. Implement Path A only.
2. Disable artwork fetching/embedding in v1.
3. Keep remux-to-m4a default (faster, lower CPU).
4. Expose job status endpoints.
5. Add one integration test that creates a tiny job and verifies status transitions.

This delivers a usable headless pipeline quickly, then you can migrate to Path B without changing API contracts.

## 10) Known limitations and failure points to plan for

- Public-data limitation:
  - No private liked songs without OAuth user scopes.
- Match accuracy:
  - YouTube search heuristics can select wrong versions (live/remix/cover).
- Platform and policy drift:
  - `yt-dlp` behavior can break when upstream platforms change.
- Runtime pressure:
  - Deep-search is CPU/network heavy; enforce max tracks/job and concurrency limits.
- Legal/compliance:
  - Ensure your usage aligns with applicable platform terms and local policy.
- Idempotency:
  - Use archive files or DB dedupe keys (`job_id + track_id`) to avoid duplicate downloads.

## 11) Concrete implementation checklist

1. Add dependencies: `yt-dlp`, `mutagen`, `ffmpeg`.
2. Create new `services/media_*` modules and extract non-UI logic from `spotify2media.py`.
3. Add SQLite job tables.
4. Add `/api/media/jobs*` endpoints.
5. Add a background worker loop (Path A), then optional worker container (Path B).
6. Add media output volume and writable paths in compose.
7. Add basic observability:
   - job duration
   - completed/failed counters
   - last error text
