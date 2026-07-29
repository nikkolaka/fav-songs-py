import { useState, useEffect, useRef } from "react"
import type { StatCardData } from "@/types"
import { fetchStats } from "@/api"
import { StatCard } from "@/components/StatCard"
import { Card } from "@/components/ui/card"
import { Pin } from "lucide-react"

interface Props {
  pinnedStats: string[]
}

export function PinnedStatsBar({ pinnedStats }: Props) {
  const [stats, setStats] = useState<StatCardData[]>([])
  const [loading, setLoading] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval>>()
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    if (!pinnedStats.length) {
      setStats([])
      setLoading(false)
      return
    }

    setLoading(true)
    const load = async () => {
      try {
        const data = await fetchStats()
        if (mounted.current) {
          setStats(data.stats.filter((s) => pinnedStats.includes(s.id)))
        }
      } catch { /* silent */ }
      if (mounted.current) setLoading(false)
    }
    load()

    pollRef.current = setInterval(load, 60_000)
    return () => {
      mounted.current = false
      clearInterval(pollRef.current)
    }
  }, [pinnedStats])

  if (!pinnedStats.length) return null

  return (
    <Card className="p-3">
      <div className="flex items-center gap-2">
        <Pin className="size-3.5 text-muted-foreground shrink-0" />
        <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">
          Pinned
        </span>

        {loading && !stats.length && (
          <div className="flex items-center gap-2">
            <div className="h-5 w-16 animate-pulse rounded bg-muted" />
            <div className="h-5 w-20 animate-pulse rounded bg-muted" />
          </div>
        )}

        {!loading && (
          <div className="flex items-center gap-2 overflow-x-auto">
            {stats.map((s) => (
              <StatCard key={s.id} stat={s} pinned={true} compact />
            ))}
          </div>
        )}
      </div>
    </Card>
  )
}
