import type { AppState, HistoryPage, HistorySummary, StatsPayload } from "@/types"

const POLL_MS = 5000

export async function fetchState(): Promise<AppState> {
  const res = await fetch("/api/state")
  if (!res.ok) throw new Error("Failed to fetch state")
  return res.json()
}

export async function fetchHistory(params: {
  q?: string
  limit?: number
  cursor?: string | null
  start?: number
  end?: number
  favorites_only?: boolean
  sort?: string
}): Promise<HistoryPage> {
  const search = new URLSearchParams()
  if (params.q) search.set("q", params.q)
  search.set("limit", String(params.limit ?? 50))
  if (params.cursor) search.set("cursor", params.cursor)
  if (params.start) search.set("start", String(params.start))
  if (params.end) search.set("end", String(params.end))
  if (params.favorites_only) search.set("favorites_only", "1")
  if (params.sort) search.set("sort", params.sort)
  const res = await fetch(`/api/history?${search}`)
  if (!res.ok) throw new Error("Failed to fetch history")
  return res.json()
}

export async function fetchHistorySummary(): Promise<HistorySummary> {
  const res = await fetch("/api/history/summary")
  if (!res.ok) throw new Error("Failed to fetch summary")
  return res.json()
}

export async function post<T = unknown>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || `Request failed (${res.status})`)
  }
  return res.status === 204 ? (null as T) : res.json()
}

export async function del(path: string): Promise<void> {
  await fetch(path, { method: "DELETE" })
}

export function pollState(cb: (state: AppState) => void): () => void {
  let active = true
  async function tick() {
    if (!active) return
    try {
      cb(await fetchState())
    } catch { /* stale class handled in component */ }
    if (active) setTimeout(tick, POLL_MS)
  }
  tick()
  return () => { active = false }
}

export async function fetchStats(): Promise<StatsPayload> {
  const res = await fetch("/api/stats")
  if (!res.ok) throw new Error("Failed to fetch stats")
  return res.json()
}

export async function togglePin(statId: string): Promise<{ pinned_stats: string[] }> {
  return post("/api/stats/toggle-pin", { stat_id: statId })
}
