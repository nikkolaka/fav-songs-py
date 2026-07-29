import { useState } from "react"
import type { AppState } from "@/types"
import { post } from "@/api"
import { Button } from "@/components/ui/button"
import { NowPlayingCard } from "@/components/NowPlayingCard"
import { FavouritesSection } from "@/components/FavouritesSection"
import { HistorySection } from "@/components/HistorySection"
import { DiscoverySection } from "@/components/DiscoverySection"
import { SettingsSection } from "@/components/SettingsSection"

interface Props {
  state: AppState
  onRefresh: () => Promise<void>
}

export function Dashboard({ state, onRefresh }: Props) {
  const [banner, setBanner] = useState<{ msg: string; isError: boolean } | null>(null)

  function notify(msg: string, isError = true) {
    setBanner({ msg, isError })
    if (!isError) setTimeout(() => setBanner(null), 3000)
  }

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn()
      await onRefresh()
    } catch (e: unknown) {
      notify(e instanceof Error ? e.message : "Something went wrong")
    }
  }

  async function toggleTracker() {
    await act(() =>
      post(state.tracker_running ? "/api/tracker/stop" : "/api/tracker/start")
    )
  }

  async function logout() {
    await act(async () => {
      await post("/api/auth/logout")
      window.location.reload()
    })
  }

  const { user, tracker_running: trackerRunning, now_playing, stats, favorites, discovery, settings } = state

  return (
    <div className="min-h-screen">
      {/* Top bar */}
      <header className="sticky top-0 z-10 border-b bg-background/80 backdrop-blur-sm">
        <div className="mx-auto flex max-w-2xl items-center justify-between gap-3 px-4 py-2.5">
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-bold tracking-tight">listen</h1>
            <span className="text-xs text-muted-foreground truncate max-w-32">
              {user.display_name}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground hidden sm:inline">
              {trackerRunning ? "Tracking" : "Paused"}
            </span>
            <Button
              variant={trackerRunning ? "default" : "outline"}
              size="sm"
              className="h-7 text-xs"
              onClick={toggleTracker}
            >
              {trackerRunning ? "Stop" : "Start"}
            </Button>
            <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={logout}>
              Log out
            </Button>
          </div>
        </div>
      </header>

      {/* Main content */}
      <div className="mx-auto max-w-2xl space-y-4 p-4">
        {banner && (
          <div
            className={`rounded-md px-4 py-2.5 text-xs ${
              banner.isError
                ? "bg-destructive/15 text-destructive"
                : "bg-primary/15 text-primary"
            }`}
          >
            {banner.msg}
          </div>
        )}

        <NowPlayingCard
          now={now_playing}
          stats={stats}
          settings={settings}
          trackerRunning={trackerRunning}
          favorites={favorites}
          onToggleTracker={toggleTracker}
        />

        <FavouritesSection favorites={favorites} act={act} />

        <HistorySection />

        <DiscoverySection discovery={discovery} act={act} />

        <SettingsSection settings={settings} act={act} onNotify={notify} />
      </div>
    </div>
  )
}
