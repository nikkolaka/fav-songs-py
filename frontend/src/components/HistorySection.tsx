import { useState, useEffect, useRef, useCallback } from "react"
import type { HistoryItem, HistorySummary } from "@/types"
import { fetchHistory, fetchHistorySummary } from "@/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Separator } from "@/components/ui/separator"
import { Star, Filter } from "lucide-react"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const PAGE = 50
const DEBOUNCE = 250

const dayFmt = new Intl.DateTimeFormat(undefined, {
  weekday: "long", day: "numeric", month: "long", year: "numeric",
})
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" })

const SORT_OPTIONS = [
  { value: "time", label: "Time" },
  { value: "name", label: "Track name" },
  { value: "artist", label: "Artist" },
  { value: "length", label: "Length" },
  { value: "completion", label: "Completion %" },
]

interface Props {
  favoriteTrackIds: string[]
}

function HistoryRow({ item, isFav }: { item: HistoryItem; isFav: boolean }) {
  const when = new Date(item.played_at)
  return (
    <li className="flex items-center gap-3 py-1.5 border-b border-border last:border-0 text-xs">
      <span className="w-12 shrink-0 text-muted-foreground tabular-nums">
        {timeFmt.format(when)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="text-sm font-medium truncate inline">
          {item.name}
          {isFav && <Star className="inline size-3 text-amber-400 fill-amber-400 align-baseline ml-1" />}
        </span>
        <span className="block text-muted-foreground truncate">{item.artist}</span>
      </span>
      <span className="w-10 shrink-0 text-right tabular-nums text-muted-foreground">
        {item.play_count ?? 0}
      </span>
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

export function HistorySection({ favoriteTrackIds }: Props) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [items, setItems] = useState<HistoryItem[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [summary, setSummary] = useState<HistorySummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")
  const [favoritesOnly, setFavoritesOnly] = useState(false)
  const [sort, setSort] = useState("time")
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const loaded = useRef(false)
  const favSet = useRef<Set<string>>(new Set(favoriteTrackIds))

  useEffect(() => {
    favSet.current = new Set(favoriteTrackIds)
  }, [favoriteTrackIds])

  const load = useCallback(async (reset: boolean) => {
    setLoading(true)
    try {
      const params: Record<string, unknown> = {
        q: query || undefined,
        limit: PAGE,
        cursor: reset ? null : cursor,
        sort,
      }
      if (dateFrom) {
        const d = new Date(dateFrom)
        params.start = d.getTime()
      }
      if (dateTo) {
        const d = new Date(dateTo + "T23:59:59")
        params.end = d.getTime()
      }
      if (favoritesOnly) params.favorites_only = true

      const page = await fetchHistory(params as Parameters<typeof fetchHistory>[0])
      setItems((prev) => reset ? page.items : [...prev, ...page.items])
      setCursor(page.next_cursor)
    } catch { /* silent */ }
    setLoading(false)
  }, [query, cursor, sort, dateFrom, dateTo, favoritesOnly])

  useEffect(() => {
    if (open && !loaded.current) {
      loaded.current = true
      load(true)
      fetchHistorySummary().then(setSummary).catch(() => {})
    }
  }, [open, load])

  useEffect(() => {
    if (!loaded.current) return
    clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      setCursor(null)
      load(true)
    }, DEBOUNCE)
    return () => clearTimeout(timer.current)
  }, [query, sort, dateFrom, dateTo, favoritesOnly]) // eslint-disable-line react-hooks/exhaustive-deps

  const grouped = groupByDate(items)

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card className="p-4">
        <CollapsibleTrigger className="flex w-full items-center justify-between hover:opacity-80">
          <h3 className="font-semibold text-sm">
            History
            {summary && !open && (
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {summary.listens.toLocaleString()} plays · {summary.qualified.toLocaleString()} counted
              </span>
            )}
          </h3>
          <span className="text-xs text-muted-foreground">{open ? "Hide" : "Show"}</span>
        </CollapsibleTrigger>

        <CollapsibleContent className="mt-3 space-y-3">
          <Separator />

          <div className="flex items-center gap-2">
            <Input
              placeholder="Search by track or artist"
              value={query}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setQuery(e.target.value)}
              className="flex-1"
            />
            <Button
              variant={filtersOpen ? "default" : "outline"}
              size="sm"
              className="h-8 text-xs gap-1.5 shrink-0"
              onClick={() => setFiltersOpen((p) => !p)}
            >
              <Filter className="size-3.5" />
              Filters
            </Button>
          </div>

          {filtersOpen && (
            <div className="space-y-3 rounded-lg border p-3">
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <label className="text-[11px] font-medium text-muted-foreground">From</label>
                  <input
                    type="date"
                    value={dateFrom}
                    onChange={(e) => setDateFrom(e.target.value)}
                    className="w-full rounded-md border border-input bg-transparent px-2.5 py-1 text-xs h-7"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-[11px] font-medium text-muted-foreground">To</label>
                  <input
                    type="date"
                    value={dateTo}
                    onChange={(e) => setDateTo(e.target.value)}
                    className="w-full rounded-md border border-input bg-transparent px-2.5 py-1 text-xs h-7"
                  />
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Checkbox
                  id="favorites-only"
                  checked={favoritesOnly}
                  onCheckedChange={(c) => setFavoritesOnly(c === true)}
                />
                <label htmlFor="favorites-only" className="text-xs cursor-pointer">
                  Favorites only
                </label>
              </div>

              <div className="flex items-center gap-2">
                <label className="text-[11px] font-medium text-muted-foreground shrink-0">Sort by</label>
                <Select value={sort} onValueChange={setSort}>
                  <SelectTrigger size="sm" className="h-7 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SORT_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {o.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}

          {items.length === 0 && !loading && (
            <p className="text-sm text-muted-foreground text-center py-8">Nothing matches.</p>
          )}

          {Array.from(grouped).map(([day, dayItems]) => (
            <div key={day}>
              <h4 className="sticky top-0 z-10 bg-card py-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {day}
              </h4>
              <ul>
                {dayItems.map((item, i) => (
                  <HistoryRow
                    key={`${item.track_id}-${item.played_at}-${i}`}
                    item={item}
                    isFav={favSet.current.has(item.track_id)}
                  />
                ))}
              </ul>
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
