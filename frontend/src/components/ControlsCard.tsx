import { useState } from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"

interface Props {
  trackerRunning: boolean
  onToggleTracker: () => void
  onLogout: () => void
  onDisconnect: () => void
}

export function ControlsCard({ trackerRunning, onToggleTracker, onLogout, onDisconnect }: Props) {
  const [open, setOpen] = useState(false)

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card className="p-4">
        <CollapsibleTrigger className="flex w-full items-center justify-between hover:opacity-80">
          <h3 className="font-semibold text-sm">Controls</h3>
          <span className="text-xs text-muted-foreground">{open ? "Hide" : "Show"}</span>
        </CollapsibleTrigger>

        <CollapsibleContent className="mt-3 space-y-4">
          <Separator />

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div className="min-w-0">
                <p className="text-sm font-medium">Tracking</p>
                <p className="text-xs text-muted-foreground">
                  {trackerRunning
                    ? "Playback is being measured live every 5 seconds."
                    : "Measurement is paused — nothing is recorded while stopped."}
                </p>
              </div>
              <Button
                variant={trackerRunning ? "destructive" : "default"}
                size="sm"
                className="h-7 text-xs shrink-0 ml-3"
                onClick={onToggleTracker}
              >
                {trackerRunning ? "Pause" : "Resume"}
              </Button>
            </div>
          </div>

          <Separator />

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div className="min-w-0">
                <p className="text-sm font-medium">Sign out</p>
                <p className="text-xs text-muted-foreground">
                  Ends this browser session. Your Spotify data and playlists are untouched.
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs shrink-0 ml-3"
                onClick={onLogout}
              >
                Sign out
              </Button>
            </div>
          </div>

          <Separator />

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div className="min-w-0">
                <p className="text-sm font-medium">Disconnect</p>
                <p className="text-xs text-muted-foreground">
                  Erases your listening history, tokens, and archive from this server.
                  Playlists already in Spotify are left alone.
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs text-destructive hover:text-destructive shrink-0 ml-3"
                onClick={onDisconnect}
              >
                Erase all data
              </Button>
            </div>
          </div>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}
