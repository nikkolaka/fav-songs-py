import { useState, useEffect, useCallback } from "react"
import type { StatCardData, StatsPayload } from "@/types"
import { fetchStats, togglePin } from "@/api"
import { Card } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Separator } from "@/components/ui/separator"
import { StatCard } from "@/components/StatCard"
import { Loader2 } from "lucide-react"

interface Props {
  pinnedStats: string[]
  onPinsChanged: () => void
}

function Skeleton() {
  return (
    <div className="grid grid-cols-2 gap-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="rounded-lg border bg-card p-3 space-y-2 animate-pulse">
          <div className="h-5 w-16 rounded bg-muted mx-auto" />
          <div className="h-3 w-24 rounded bg-muted mx-auto" />
          <div className="h-3 w-20 rounded bg-muted mx-auto" />
        </div>
      ))}
    </div>
  )
}

export function StatisticsSection({ pinnedStats, onPinsChanged }: Props) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<StatsPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [toggling, setToggling] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await fetchStats())
    } catch { /* silent */ }
    setLoading(false)
  }, [])

  useEffect(() => {
    if (open && !data) {
      load()
    }
  }, [open, data, load])

  const handleToggle = async (id: string) => {
    setToggling(id)
    try {
      const res = await togglePin(id)
      if (res.pinned_stats) {
        onPinsChanged()
      }
    } catch { /* silent */ }
    setToggling(null)
  }

  const stats: StatCardData[] = data?.stats ?? []
  const pinnedCount = pinnedStats.length

  const sorted = [...stats].sort((a, b) => {
    const aPinned = pinnedStats.includes(a.id)
    const bPinned = pinnedStats.includes(b.id)
    if (aPinned && !bPinned) return -1
    if (!aPinned && bPinned) return 1
    return stats.indexOf(a) - stats.indexOf(b)
  })

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card className="p-4">
        <CollapsibleTrigger className="flex w-full items-center justify-between hover:opacity-80">
          <h3 className="font-semibold text-sm">
            Statistics
            {pinnedCount > 0 && (
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {pinnedCount} pinned
              </span>
            )}
          </h3>
          <span className="text-xs text-muted-foreground">{open ? "Hide" : "Show"}</span>
        </CollapsibleTrigger>

        <CollapsibleContent className="mt-3 space-y-3">
          <Separator />

          {loading && <Skeleton />}

          {!loading && stats.length === 0 && (
            <div className="flex items-center justify-center gap-2 text-muted-foreground py-8">
              <Loader2 className="size-4" />
              <span className="text-sm">Nothing to show yet. Keep listening.</span>
            </div>
          )}

          {!loading && stats.length > 0 && (
            <div className="grid grid-cols-2 gap-3">
              {sorted.map((s) => (
                <div key={s.id} className={toggling === s.id ? "opacity-50" : ""}>
                  <StatCard
                    stat={s}
                    pinned={pinnedStats.includes(s.id)}
                    onTogglePin={handleToggle}
                  />
                </div>
              ))}
            </div>
          )}
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}
