export interface NowPlaying {
  track_id: string
  name: string
  artist: string
  duration_ms: number
  progress_ms: number
  completion_ratio: number
  heard_ms: number
  heard_ratio: number
  counts: boolean
  is_playing: boolean
}

export interface Settings {
  favorite_threshold: number
  min_completion_ratio: number
  playlist_name: string
  playlist_public: boolean
  auto_add_enabled: boolean
  discovery_enabled: boolean
  favorites_playlist_id: string | null
}

export interface Stats {
  last_24h: { total: number; qualified: number }
  tracked_tracks: number
  next_favorite: {
    track_id: string
    name: string
    artist: string
    qualified_plays: number
  } | null
}

export interface Favorite {
  track_id: string
  name: string
  artist: string
  qualified_plays: number
  total_plays: number
  last_played: string
  in_playlist: boolean
}

export interface DiscoveryItem {
  track_id: string
  name: string
  artist: string
  added_at: number
}

export interface DiscoveryBlocked {
  track_id: string
  name: string
  artist: string
  reason: string
  blocked_at: string
}

export interface DiscoveryMonth {
  month: string
  name: string
  tracks: number
}

export interface DiscoverySource {
  playlist_id: string
  label: string
  degraded: string | null
}

export interface UnlabelledContext {
  playlist_id: string
  sample_track: string
  title: string | null
  play_count: number
  last_seen: string
}

export interface Discovery {
  month: string
  month_name: string
  tracks: DiscoveryItem[]
  blocked: DiscoveryBlocked[]
  months: DiscoveryMonth[]
  sources: DiscoverySource[]
  unlabelled: UnlabelledContext[]
  blocklist: {
    artists: number | null
    fetched_at: number | null
    error: string | null
  }
}

export interface AppState {
  connected: boolean
  user: { display_name: string; spotify_user_id: string }
  tracker_running: boolean
  last_error: string | null
  now_playing: NowPlaying | null
  settings: Settings
  stats: Stats
  favorites: Favorite[]
  discovery: Discovery
}

export interface HistoryItem {
  track_id: string
  name: string
  artist: string
  played_at: string
  completion_ratio: number | null
  qualified: boolean
  is_open: boolean
}

export interface HistoryPage {
  items: HistoryItem[]
  next_cursor: string | null
}

export interface HistorySummary {
  listens: number
  qualified: number
  tracks: number
  first_played: string
}
