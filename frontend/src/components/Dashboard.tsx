import { useState } from "react"
import type { AppState } from "@/types"
import { post } from "@/api"
import { NowPlayingCard } from "@/components/NowPlayingCard"
import { PinnedStatsBar } from "@/components/PinnedStatsBar"
import { FavouritesSection } from "@/components/FavouritesSection"
import { HistorySection } from "@/components/HistorySection"
import { StatisticsSection } from "@/components/StatisticsSection"
import { DiscoverySection } from "@/components/DiscoverySection"
import { SettingsSection } from "@/components/SettingsSection"
import { ControlsCard } from "@/components/ControlsCard"

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

  async function disconnect() {
    if (!confirm("Erase your listening history, archive and tokens from this server?")) return
    await act(async () => {
      await fetch("/api/auth/disconnect", { method: "POST" })
      window.location.reload()
    })
  }

  const { user, tracker_running: trackerRunning, now_playing, favorites, discovery, settings } = state
  const pinned = (settings.pinned_stats ?? []) as string[]

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
          <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span
              className={`inline-block size-1.5 rounded-full ${
                trackerRunning ? "bg-emerald-400" : "bg-muted-foreground/40"
              }`}
            />
            {trackerRunning ? "Tracking" : "Paused"}
          </span>
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

        <NowPlayingCard now={now_playing} />

        <PinnedStatsBar pinnedStats={pinned} />

        <StatisticsSection
          pinnedStats={pinned}
          onPinsChanged={onRefresh}
        />

        <FavouritesSection favorites={favorites} act={act} />

        <HistorySection />

        <DiscoverySection discovery={discovery} act={act} />

        <SettingsSection settings={settings} act={act} onNotify={notify} />

        <ControlsCard
          trackerRunning={trackerRunning}
          onToggleTracker={toggleTracker}
          onLogout={logout}
          onDisconnect={disconnect}
        />
      </div>
    </div>
  )
}
