import { useRef, useState, useEffect } from "react"
import type { NowPlaying } from "@/types"
import { Card } from "@/components/ui/card"
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
  const indicatorRef = useRef<HTMLDivElement>(null)
  const frameRef = useRef(0)
  const [, tick] = useState(0)

  // Update anchor when server data arrives — does not re-render the bar
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
  }, [now?.progress_ms, now?.heard_ms, now?.is_playing, now?.duration_ms]) // eslint-disable-line react-hooks/exhaustive-deps

  // Smooth bar: rAF directly updates the DOM, zero React re-renders
  useEffect(() => {
    let running = true

    function frame() {
      if (!running) return
      const a = anchor.current
      const el = indicatorRef.current
      if (el && a) {
        if (a.is_playing) {
          const elapsed = Date.now() - a.received_at
          const pct = Math.min(1, (a.progress_ms + elapsed) / (a.duration_ms || 1))
          el.style.transform = `translateX(-${(1 - pct) * 100}%)`
        }
      }
      frameRef.current = requestAnimationFrame(frame)
    }
    frameRef.current = requestAnimationFrame(frame)
    return () => {
      running = false
      cancelAnimationFrame(frameRef.current)
    }
  }, [])

  // Text-only tick: time display updates once per second
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [])

  const a = anchor.current
  let heard = now?.heard_ms ?? 0
  let duration = now?.duration_ms ?? 0
  let isPlaying = now?.is_playing ?? false

  if (now && a && a.is_playing) {
    const elapsed = Date.now() - a.received_at
    heard = Math.min(a.heard_ms + elapsed, a.duration_ms || a.heard_ms + elapsed)
    duration = a.duration_ms || duration
  } else if (a) {
    heard = a.heard_ms
    duration = a.duration_ms
    isPlaying = a.is_playing
  }

  return (
    <Card className="p-4">
      <h3 className="font-semibold text-sm">Now playing</h3>

      {now ? (
        <>
          <Separator className="my-3" />

          <div>
            <p className="font-medium truncate">{now.name}</p>
            <p className="text-sm text-muted-foreground truncate">{now.artist}</p>
          </div>

          {/* Custom smooth bar — rAF drives the fill, no React renders */}
          <div className="relative mt-3 h-1 w-full overflow-hidden rounded-full bg-muted">
            <div
              ref={indicatorRef}
              className="size-full flex-1 bg-primary will-change-transform"
              style={{ transform: "translateX(-100%)" }}
            />
          </div>

          <div className="flex items-center justify-between text-xs mt-2">
            <span className="text-muted-foreground tabular-nums">
              {clock(heard)} / {clock(duration)}
            </span>
            <span className={isPlaying ? "text-success" : "text-muted-foreground"}>
              {isPlaying ? "Listening" : "Paused"}
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
