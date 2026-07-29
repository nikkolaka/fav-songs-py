import { useRef, useState, useEffect } from "react"
import type { NowPlaying } from "@/types"
import { Card } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { Music } from "lucide-react"

function clock(ms: number) {
  const total = Math.round(ms / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`
}

interface Props {
  now: NowPlaying | null
}

interface Anchor {
  progress_ms: number
  heard_ms: number
  duration_ms: number
  is_playing: boolean
  received_at: number
}

export function NowPlayingCard({ now }: Props) {
  const anchor = useRef<Anchor | null>(null)
  const [, tick] = useState(0)

  useEffect(() => {
    if (now) {
      anchor.current = {
        progress_ms: now.progress_ms,
        heard_ms: now.heard_ms ?? 0,
        duration_ms: now.duration_ms ?? 0,
        is_playing: now.is_playing,
        received_at: Date.now(),
      }
    } else {
      anchor.current = null
    }
  }, [now?.progress_ms, now?.heard_ms, now?.is_playing, now?.duration_ms])

  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [])

  const a = anchor.current
  let display = now

  if (now && a && a.is_playing) {
    const elapsed = Date.now() - a.received_at
    const estProgress = a.progress_ms + elapsed
    const estHeard = a.heard_ms + elapsed
    const dur = a.duration_ms || 1
    display = {
      ...now,
      progress_ms: Math.min(estProgress, dur),
      heard_ms: Math.min(estHeard, dur),
      completion_ratio: Math.min(estProgress / dur, 1),
      heard_ratio: Math.min(estHeard / dur, 1),
    }
  }

  return (
    <Card className="p-4">
      <h3 className="font-semibold text-sm">Now playing</h3>

      {display ? (
        <>
          <Separator className="my-3" />

          <div>
            <p className="font-medium truncate">{display.name}</p>
            <p className="text-sm text-muted-foreground truncate">{display.artist}</p>
          </div>

          <Progress
            className="mt-3"
            value={Math.round((display.completion_ratio || 0) * 100)}
          />

          <div className="flex items-center justify-between text-xs mt-2">
            <span className="text-muted-foreground tabular-nums">
              {clock(display.heard_ms ?? 0)} / {clock(display.duration_ms ?? 0)}
            </span>
            <span className={display.is_playing ? "text-emerald-500" : "text-muted-foreground"}>
              {display.is_playing ? "Listening" : "Paused"}
            </span>
          </div>
        </>
      ) : (
        <div className="flex items-center gap-2 text-muted-foreground py-1">
          <Music className="size-4" />
          <span className="text-sm">Nothing playing.</span>
        </div>
      )}
    </Card>
  )
}
