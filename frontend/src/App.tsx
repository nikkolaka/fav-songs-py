import { useEffect, useState, useRef, useCallback } from "react"
import type { AppState } from "@/types"
import { fetchState, pollState } from "@/api"
import { LoginPage } from "@/components/LoginPage"
import { Dashboard } from "@/components/Dashboard"

export default function App() {
  const [state, setState] = useState<AppState | null>(null)
  const [stale, setStale] = useState(false)
  const mounted = useRef(false)

  const refresh = useCallback(async () => {
    try {
      setState(await fetchState())
      setStale(false)
    } catch {
      setStale(true)
    }
  }, [])

  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true
      refresh()
      return pollState((s) => { setState(s); setStale(false) })
    }
  }, [refresh])

  useEffect(() => {
    if (stale) document.body.classList.add("stale")
    else document.body.classList.remove("stale")
  }, [stale])

  if (!state) return null
  if (!state.connected) return <LoginPage />

  return <Dashboard state={state} onRefresh={refresh} />
}
