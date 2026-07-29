import type { NowPlaying, Stats, Settings, Favorite } from "@/types"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { Music, Play } from "lucide-react"

function clock(ms: number) {
  const total = Math.round(ms / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`
}

interface Props {
  now: NowPlaying | null
  stats: Stats
  settings: Settings
  trackerRunning: boolean
  favorites: Favorite[]
  onToggleTracker: () => void
}

export function NowPlayingCard({ now, stats, settings, trackerRunning, onToggleTracker }: Props) {
  const day = stats.last_24h ?? { total: 0, qualified: 0 }

  return (
    <Card className="space-y-4 p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-sm">Now playing</h3>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {trackerRunning ? "Tracking" : "Paused"}
          </span>
          <Button
            variant={trackerRunning ? "default" : "outline"}
            size="sm"
            onClick={onToggleTracker}
          >
            {trackerRunning ? <Play className="size-3" /> : null}
            {trackerRunning ? "Stop" : "Start"}
          </Button>
        </div>
      </div>

      <Separator />

      {/* Now playing track */}
      {now ? (
        <div className="space-y-3">
          <div>
            <p className="font-medium">{now.name}</p>
            <p className="text-sm text-muted-foreground">{now.artist}</p>
          </div>

          <Progress value={Math.round((now.completion_ratio || 0) * 100)} />

          <div className="flex items-center justify-between text-xs">
            <span className={now.counts ? "text-emerald-500 font-medium" : "text-muted-foreground"}>
              {now.counts
                ? "Counts"
                : `${Math.round(settings.min_completion_ratio * 100)}% needed to count`}
            </span>
            <span className="text-muted-foreground">
              heard {clock(now.heard_ms ?? 0)} of {clock(now.duration_ms ?? 0)} ({Math.round((now.heard_ratio || 0) * 100)}%)
              {!now.is_playing && " · paused"}
            </span>
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Music className="size-4" />
          <span className="text-sm">Nothing playing.</span>
        </div>
      )}

      <Separator />

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4 text-center">
        <div>
          <p className="text-2xl font-bold tabular-nums">{day.qualified}</p>
          <p className="text-xs text-muted-foreground">Counted in 24h</p>
          <p className="text-xs text-muted-foreground">of {day.total} played</p>
        </div>
        <div>
          <p className="text-2xl font-bold tabular-nums">{stats.tracked_tracks}</p>
          <p className="text-xs text-muted-foreground">Tracks seen</p>
        </div>
        <div>
          {stats.next_favorite ? (
            <>
              <p className="text-sm font-medium truncate">{stats.next_favorite.name}</p>
              <p className="text-xs text-muted-foreground">
                {settings.favorite_threshold - stats.next_favorite.qualified_plays} to go
              </p>
            </>
          ) : (
            <p className="text-xs text-muted-foreground">—</p>
          )}
          <p className="text-xs text-muted-foreground mt-0.5">Next up</p>
        </div>
      </div>
    </Card>
  )
}
