import { useState, useEffect, useRef, useMemo } from "react"
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
  const loadedOnce = useRef(false)

  const pinnedKey = useMemo(
    () => pinnedStats.slice().sort().join(","),
    [pinnedStats],
  )

  useEffect(() => {
    mounted.current = true

    if (!pinnedKey) {
      setStats([])
      setLoading(false)
      loadedOnce.current = false
      clearInterval(pollRef.current)
      return
    }

    if (!loadedOnce.current) {
      setLoading(true)
    }

    const load = async () => {
      try {
        const data = await fetchStats()
        if (mounted.current) {
          const current = pinnedStats
          setStats(data.stats.filter((s) => current.includes(s.id)))
        }
      } catch { /* silent */ }
      if (mounted.current) {
        setLoading(false)
        loadedOnce.current = true
      }
    }
    load()

    clearInterval(pollRef.current)
    pollRef.current = setInterval(load, 60_000)

    return () => {
      mounted.current = false
      clearInterval(pollRef.current)
    }
  }, [pinnedKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const showSkeleton = loading && !loadedOnce.current && stats.length === 0

  return (
    <Card className="p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Pin className="size-3.5 text-muted-foreground shrink-0" />

        {showSkeleton && (
          <div className="flex items-center gap-2">
            <div className="h-5 w-16 animate-pulse rounded bg-muted" />
            <div className="h-5 w-20 animate-pulse rounded bg-muted" />
          </div>
        )}

        {!showSkeleton && (
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
