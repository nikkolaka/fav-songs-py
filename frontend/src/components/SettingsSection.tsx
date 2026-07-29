import { useState } from "react"
import type { Settings } from "@/types"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Slider } from "@/components/ui/slider"
import { Separator } from "@/components/ui/separator"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"

interface Props {
  settings: Settings
  act: (fn: () => Promise<unknown>) => void
  onNotify: (msg: string, isError?: boolean) => void
}

export function SettingsSection({ settings, act, onNotify }: Props) {
  const [open, setOpen] = useState(false)
  const [ratio, setRatio] = useState(settings.min_completion_ratio)
  const [threshold, setThreshold] = useState(settings.favorite_threshold)
  const [name, setName] = useState(settings.playlist_name)
  const [autoAdd, setAutoAdd] = useState(settings.auto_add_enabled)
  const [discovery, setDiscovery] = useState(settings.discovery_enabled)
  const [publicList, setPublicList] = useState(settings.playlist_public)

  async function save(e: React.FormEvent) {
    e.preventDefault()
    await act(() =>
      fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          favorite_threshold: threshold,
          min_completion_ratio: ratio,
          playlist_name: name.trim(),
          auto_add_enabled: autoAdd,
          discovery_enabled: discovery,
          playlist_public: publicList,
        }),
      })
    )
    onNotify("Settings saved.", false)
  }

  async function disconnect() {
    if (!confirm("Erase your listening history, archive and tokens from this server?")) return
    await act(async () => {
      await fetch("/api/auth/disconnect", { method: "POST" })
      window.location.reload()
    })
  }

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card className="p-4">
        <CollapsibleTrigger className="flex w-full items-center justify-between hover:opacity-80">
          <h3 className="font-semibold text-sm">Settings</h3>
          <span className="text-xs text-muted-foreground">{open ? "Hide" : "Show"}</span>
        </CollapsibleTrigger>

        <CollapsibleContent className="mt-3 space-y-4">
          <Separator />

          <form onSubmit={save} className="space-y-4">
            <div className="space-y-2">
              <Label className="text-xs font-medium">
                Listening threshold — {Math.round(ratio * 100)}% of a track
              </Label>
              <Slider
                value={[ratio]}
                min={0.25}
                max={1}
                step={0.05}
                onValueChange={([v]) => setRatio(v)}
              />
              <p className="text-xs text-muted-foreground">
                How much of a track you have to actually hear before that play counts.
              </p>
            </div>

            <div className="space-y-1">
              <Label className="text-xs font-medium" htmlFor="threshold">Plays to favourite</Label>
              <Input
                id="threshold"
                type="number"
                min={1}
                max={100}
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
                className="h-8 text-sm"
              />
            </div>

            <div className="space-y-1">
              <Label className="text-xs font-medium" htmlFor="playlist-name">Playlist name</Label>
              <Input
                id="playlist-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="h-8 text-sm"
                maxLength={100}
              />
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-xs">Add favourites to playlist automatically</Label>
                <Switch checked={autoAdd} onCheckedChange={setAutoAdd} />
              </div>
              <div className="flex items-center justify-between">
                <Label className="text-xs">Archive discoveries to monthly playlist</Label>
                <Switch checked={discovery} onCheckedChange={setDiscovery} />
              </div>
              <div className="flex items-center justify-between">
                <Label className="text-xs">List new playlists on Spotify profile</Label>
                <Switch checked={publicList} onCheckedChange={setPublicList} />
              </div>
            </div>

            <Button type="submit" size="sm">Save settings</Button>
          </form>

          <Separator />

          <Collapsible>
            <CollapsibleTrigger className="text-xs text-muted-foreground hover:underline">
              Disconnect
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2 space-y-2">
              <p className="text-xs text-muted-foreground">
                Removes your tokens, listening history and archive from this server.
                Playlists already in your Spotify account are left alone.
              </p>
              <Button variant="outline" size="sm" onClick={disconnect}>
                Disconnect and erase my data
              </Button>
            </CollapsibleContent>
          </Collapsible>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}
