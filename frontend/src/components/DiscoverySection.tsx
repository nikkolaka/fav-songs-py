import { useState } from "react"
import type { Discovery } from "@/types"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"

interface Props {
  discovery: Discovery
  act: (fn: () => Promise<unknown>) => void
}

function ago(ms: number) {
  const s = Math.max(0, Math.floor((Date.now() - ms) / 1000))
  if (s < 90) return "just now"
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export function DiscoverySection({ discovery, act }: Props) {
  const [open, setOpen] = useState(false)

  async function addSource(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = e.currentTarget
    const playlist = (form.elements.namedItem("source-input") as HTMLInputElement).value.trim()
    const label = (form.elements.namedItem("source-label") as HTMLInputElement).value.trim() || "Discover Weekly"
    if (!playlist) return
    await act(() =>
      fetch("/api/discovery/sources", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ playlist, label }),
      })
    )
    ;(form.elements.namedItem("source-input") as HTMLInputElement).value = ""
  }

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card className="p-4">
        <CollapsibleTrigger className="flex w-full items-center justify-between hover:opacity-80">
          <h3 className="font-semibold text-sm">{discovery.month_name}</h3>
          <span className="text-xs text-muted-foreground">{open ? "Hide" : "Show"}</span>
        </CollapsibleTrigger>

        <CollapsibleContent className="mt-3 space-y-3">
          <Separator />

          {/* Tracks */}
          {discovery.tracks.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing archived this month yet.
            </p>
          ) : (
            <ul className="space-y-1">
              {discovery.tracks.map((t) => (
                <li key={t.track_id} className="flex items-center justify-between text-sm py-1 border-b border-border last:border-0">
                  <div>
                    <p className="font-medium">{t.name}</p>
                    <p className="text-xs text-muted-foreground">{t.artist}</p>
                  </div>
                  <span className="text-xs text-muted-foreground">{ago(t.added_at * 1000)}</span>
                </li>
              ))}
            </ul>
          )}

          {/* Blocked tracks */}
          {discovery.blocked.length > 0 && (
            <Collapsible>
              <CollapsibleTrigger className="text-xs text-muted-foreground hover:underline">
                Filtered as AI-generated ({discovery.blocked.length})
              </CollapsibleTrigger>
              <CollapsibleContent className="mt-2 space-y-1">
                {discovery.blocked.map((b, i) => (
                  <p key={i} className="text-xs text-muted-foreground">
                    {b.name} — {b.artist} ({b.reason})
                  </p>
                ))}
              </CollapsibleContent>
            </Collapsible>
          )}

          {/* Sources & past months */}
          <Collapsible>
            <CollapsibleTrigger className="text-xs text-muted-foreground hover:underline">
              Sources & past months
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2 space-y-3">
              {discovery.blocklist.artists && (
                <p className="text-xs text-muted-foreground">
                  AI blocklist: {discovery.blocklist.artists.toLocaleString()} artists,
                  updated {ago(discovery.blocklist.fetched_at! * 1000)}
                  {discovery.blocklist.error && ` — ${discovery.blocklist.error}`}
                </p>
              )}
              {discovery.sources.map((s) => (
                <div key={s.playlist_id} className="flex items-center justify-between text-xs">
                  <span>
                    {s.label}
                    {s.degraded && (
                      <span className="text-destructive ml-1">({s.degraded})</span>
                    )}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => act(() =>
                      fetch(`/api/discovery/sources/${encodeURIComponent(s.playlist_id)}`, {
                        method: "DELETE",
                      })
                    )}
                  >
                    Remove
                  </Button>
                </div>
              ))}

              <form onSubmit={addSource} className="flex flex-col sm:flex-row gap-2">
                <Input
                  name="source-input"
                  placeholder="Paste a Discover Weekly link"
                  className="h-8 text-xs"
                />
                <Input
                  name="source-label"
                  placeholder="Label"
                  defaultValue="Discover Weekly"
                  className="h-8 w-full sm:w-28 text-xs"
                />
                <Button type="submit" size="sm" className="h-8 text-xs shrink-0">Add</Button>
              </form>

              {discovery.months.map((m) => (
                <p key={m.month} className="text-xs text-muted-foreground">
                  {m.name} — {m.tracks} tracks
                </p>
              ))}
            </CollapsibleContent>
          </Collapsible>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}
