import { useState, useEffect, useRef, useCallback } from "react"
import type { HistoryItem, HistorySummary } from "@/types"
import { fetchHistory, fetchHistorySummary } from "@/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"

const PAGE = 50
const DEBOUNCE = 250

const dayFmt = new Intl.DateTimeFormat(undefined, {
  weekday: "long", day: "numeric", month: "long", year: "numeric",
})
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" })

function HistoryRow({ item }: { item: HistoryItem }) {
  const when = new Date(item.played_at)
  const pct = Math.round((item.completion_ratio || 0) * 100)
  return (
    <li className="flex items-center gap-3 py-1.5 border-b border-border last:border-0">
      <span className="w-18 shrink-0 text-xs text-muted-foreground tabular-nums">
        {timeFmt.format(when)}
      </span>
      <span className="min-w-0 flex-1">
        <p className="text-sm font-medium truncate">{item.name}</p>
        <p className="text-xs text-muted-foreground truncate">{item.artist}</p>
      </span>
      {item.is_open ? (
        <Badge variant="default" className="text-xs">playing</Badge>
      ) : item.qualified ? (
        <Badge variant="default" className="bg-emerald-500/15 text-emerald-500 hover:bg-emerald-500/20 text-xs">
          {pct}%
        </Badge>
      ) : (
        <Badge variant="secondary" className="text-xs">{pct}%</Badge>
      )}
    </li>
  )
}

function groupByDate(items: HistoryItem[]): Map<string, HistoryItem[]> {
  const map = new Map<string, HistoryItem[]>()
  for (const item of items) {
    const day = dayFmt.format(new Date(item.played_at))
    const group = map.get(day)
    if (group) group.push(item)
    else map.set(day, [item])
  }
  return map
}

export function HistorySection() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [items, setItems] = useState<HistoryItem[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [summary, setSummary] = useState<HistorySummary | null>(null)
  const [loading, setLoading] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const loaded = useRef(false)

  const load = useCallback(async (reset: boolean) => {
    setLoading(true)
    try {
      const page = await fetchHistory({
        q: query || undefined,
        limit: PAGE,
        cursor: reset ? null : cursor,
      })
      setItems((prev) => reset ? page.items : [...prev, ...page.items])
      setCursor(page.next_cursor)
    } catch { /* silent */ }
    setLoading(false)
  }, [query, cursor])

  // Initial load when opened
  useEffect(() => {
    if (open && !loaded.current) {
      loaded.current = true
      load(true)
      fetchHistorySummary().then(setSummary).catch(() => {})
    }
  }, [open, load])

  // Debounced search
  useEffect(() => {
    if (!loaded.current) return
    clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      setCursor(null)
      load(true)
    }, DEBOUNCE)
    return () => clearTimeout(timer.current)
  }, [query]) // eslint-disable-line react-hooks/exhaustive-deps

  const grouped = groupByDate(items)

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card className="p-4">
        <CollapsibleTrigger className="flex w-full items-center justify-between hover:opacity-80">
          <h3 className="font-semibold text-sm">
            History
            {summary && (
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {summary.listens.toLocaleString()} plays · {summary.qualified.toLocaleString()} counted
              </span>
            )}
          </h3>
          <span className="text-xs text-muted-foreground">{open ? "Hide" : "Show"}</span>
        </CollapsibleTrigger>

        <CollapsibleContent className="mt-3 space-y-3">
          <Separator />
          <Input
            placeholder="Search by track or artist"
            value={query}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setQuery(e.target.value)}
          />

          {items.length === 0 && !loading && (
            <p className="text-sm text-muted-foreground text-center py-8">Nothing matches.</p>
          )}

          {Array.from(grouped).map(([day, dayItems]) => (
            <div key={day}>
              <h4 className="sticky top-0 z-10 bg-card py-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {day}
              </h4>
              <ul>{dayItems.map((item, i) => <HistoryRow key={`${item.track_id}-${i}`} item={item} />)}</ul>
            </div>
          ))}

          {cursor && (
            <div className="flex justify-center pt-2">
              <Button variant="outline" size="sm" disabled={loading} onClick={() => load(false)}>
                {loading ? "Loading..." : "Load more"}
              </Button>
            </div>
          )}
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}
