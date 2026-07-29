import { useState } from "react"
import type { AppState } from "@/types"
import { post } from "@/api"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
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
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="hidden w-56 shrink-0 border-r bg-sidebar p-4 md:flex md:flex-col justify-between">
        <div className="space-y-4">
          <div>
            <h1 className="text-lg font-bold tracking-tight">listen</h1>
            <p className="text-muted-foreground text-xs">
              Songs you actually listen to become a playlist.
            </p>
          </div>
          <Separator />
          <nav className="space-y-1 text-sm">
            <a href="#now" className="block rounded-md px-3 py-1.5 hover:bg-accent">
              Now
            </a>
            <a href="#favourites" className="block rounded-md px-3 py-1.5 hover:bg-accent">
              Favourites
            </a>
            <a href="#history" className="block rounded-md px-3 py-1.5 hover:bg-accent">
              History
            </a>
            <a href="#discovery" className="block rounded-md px-3 py-1.5 hover:bg-accent">
              Discovery
            </a>
            <a href="#settings" className="block rounded-md px-3 py-1.5 hover:bg-accent">
              Settings
            </a>
          </nav>
        </div>
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground truncate">{user.display_name}</p>
          <Button variant="outline" size="sm" className="w-full" onClick={logout}>
            Log out
          </Button>
        </div>
      </aside>

      {/* Mobile header */}
      <header className="fixed inset-x-0 top-0 z-10 border-b bg-background p-3 md:hidden">
        <div className="flex items-center justify-between">
          <h1 className="font-bold">listen</h1>
          <Button variant="outline" size="sm" onClick={logout}>Log out</Button>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl space-y-6 p-4 pt-14 md:pt-6">
          {banner && (
            <div
              className={`rounded-md px-4 py-3 text-sm ${
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

          <div id="history">
            <HistorySection />
          </div>

          <div id="discovery">
            <DiscoverySection discovery={discovery} act={act} />
          </div>

          <div id="settings">
            <SettingsSection settings={settings} act={act} onNotify={notify} />
          </div>
        </div>
      </main>
    </div>
  )
}
