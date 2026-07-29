import { useRef, useState, useEffect, useCallback } from "react"
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
  const frame = useRef(0)
  const [, tick] = useState(0)

  const interpolate = useCallback(() => {
    tick((n) => n + 1)
    frame.current = requestAnimationFrame(interpolate)
  }, [])

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
    frame.current = requestAnimationFrame(interpolate)
    return () => cancelAnimationFrame(frame.current)
  }, [interpolate])

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
    <Card className="space-y-4 p-4">
      <h3 className="font-semibold text-sm">Now playing</h3>

      {display ? (
        <>
          <Separator />
          <div className="space-y-3">
            <div>
              <p className="font-medium">{display.name}</p>
              <p className="text-sm text-muted-foreground">{display.artist}</p>
            </div>

            <Progress value={Math.round((display.completion_ratio || 0) * 100)} />

            <div className="flex items-center justify-between text-xs">
              <span className={display.counts ? "text-emerald-500 font-medium" : "text-muted-foreground"}>
                {display.counts ? "Counts" : "Not counting"}
              </span>
              <span className="text-muted-foreground">
                heard {clock(display.heard_ms ?? 0)} of {clock(display.duration_ms ?? 0)} ({Math.round((display.heard_ratio || 0) * 100)}%)
                {!display.is_playing && " · paused"}
              </span>
            </div>
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
