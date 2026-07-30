# Features

A Spotify listening tracker. Measures how much of each track you actually hear, counts the ones you finish, and auto-curates a favourites playlist.

---

## Listening Measurement

Polls `GET /me/player` every 5 seconds to measure playback as it happens. A *session* is one continuous play through a track. Each poll credits the audio that elapsed, capped at actual wall-clock time — so seeking forward doesn't inflate your stats.

When a session closes (track changes or playback stops), the unobserved tail is credited up to the track's remaining duration, so a full play-through always reaches a 100% completion ratio.

A play *counts* (qualifies) when the completion ratio meets or exceeds your configured threshold (default 80%). Only counted plays move a track toward becoming a favourite.

## Favourites Playlist

Every counted play increments a per-track tally. When a track reaches your configured play threshold (default 5), it's automatically added to your favourites playlist on Spotify.

If you lower the threshold or turn auto-add back on, a reconcile pass catches up any tracks that already qualify. Tracks added manually to the playlist are also recognised and preserved.

You can remove tracks from the favourites playlist through the UI (multi-select, then remove).

**Scope:** Curates one Spotify playlist. You choose the name and whether it's public.

## Listening History

Every measured listen is permanently recorded with its completion ratio, whether it counted, the time it was played, and the context (playlist/album/artist) it came from.

The history is searchable (full-text on track/artist), filterable by date range, sortable by time/name/artist/length/completion, and filterable to show only favourites.

Pagination is keyset-based — page 500 costs the same as page 1.

**Scope:** The history grows without bound. Nothing is ever pruned or aggregated away. It's your complete measured listening record.

## Statistics

A set of computed stats you can pin to a persistent bar at the top of the dashboard:

| Stat | Description |
|---|---|
| Counted Today | Qualified/total plays in the last 24 hours |
| Next Favorite | The track closest to the favourite threshold, with artist |
| All-Time Plays | Total listens ever recorded |
| Listening Time | Total hours:minutes tracked |
| Favorites | Number of tracks over the favourite threshold |
| Top Artist | Artist with the most counted plays |
| Current Streak | Consecutive days with at least one listen |
| Longest Streak | All-time best daily streak |
| Peak Hour | Your most active hour of the day |
| Avg Completion | Average listen-through percentage |
| Daily Avg (7d) | Average daily listening hours over the last week |

Stats update every 60 seconds. Click any stat card to pin/unpin it from the top bar.

**Scope:** All stats are computed from your local listen history. No data is sent anywhere. No Spotify API calls.

## Discovery Archive

Scrapes your Discover Weekly playlist via its public embed page. Runs automatically on Mondays (when Discover Weekly refreshes) and archives every new track into a monthly playlist on Spotify.

Tracks by artists on the live AI blocklist are filtered out before anything is written. Filtered tracks are recorded in the audit log so you can verify nothing legitimate was dropped.

**Scope:** One playlist — Discover Weekly. No other sources. No play-based capture. Manual source registration is available through the UI if you want other playlists, but only ones labeled "Discover Weekly" are swept.

### AI Blocklist

Fetches a community-maintained CSV of AI-generated artists from GitHub daily. Cached on disk so a GitHub outage doesn't disable the filter. Matching is by Spotify artist ID, with name-based fallback when the ID can't be resolved.

## Now Playing

Shows the track currently playing with a smooth progress bar that updates at 60fps via direct DOM animation (no React re-renders). Displays elapsed time, total duration, and whether playback is active or paused.

## Dashboard Layout

- **Header:** App name, your display name, and a tracking status indicator (green dot = tracking, gray = paused).
- **Pinned Stats Bar:** Your chosen statistics, always visible if any are pinned.
- **Now Playing Card:** The current track.
- **Statistics Section:** Collapsible grid of all stats. Click to pin/unpin.
- **History Section:** Collapsible searchable history with filters.
- **Discovery Section:** Current month's discoveries, blocked tracks, sources, and past months.
- **Settings:** Threshold, playlist name, toggles.
- **Controls:** Pause/resume tracking, sign out, disconnect — all with explanatory labels.

## Controls

- **Pause/Resume:** Stops or starts the 5-second polling loop. When paused, nothing is measured and nothing new appears in history.
- **Sign out:** Ends the browser session. Your Spotify data and playlists are untouched.
- **Disconnect:** Erases your listening history, tokens, and archive from this server. Playlists already in your Spotify account are left alone.

## Settings

| Setting | Default | Description |
|---|---|---|
| Listening threshold | 80% | How much of a track you must hear for the play to count |
| Plays to favourite | 5 | How many counted plays before a track is added to the playlist |
| Playlist name | "Favourite Songs" | Name of the Spotify playlist |
| Auto-add | On | Automatically add qualifying tracks to the playlist |
| Discovery archive | On | Archive Discover Weekly tracks to a monthly playlist |
| Public playlists | Off | Whether new playlists appear on your Spotify profile |

## Data Storage

Everything lives in a local SQLite database. Nothing is sent to a third party. Spotify tokens are encrypted at rest (Fernet, key derived from your session secret).
