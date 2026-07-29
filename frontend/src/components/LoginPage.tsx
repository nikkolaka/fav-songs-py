import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { post } from "@/api"

const LOGIN_ERRORS: Record<string, string> = {
  invalid_state: "That login link expired. Try again.",
  exchange_failed: "Spotify rejected the login. Try again.",
  full: "This app already has its five Spotify accounts. Development Mode allows no more.",
  access_denied: "You declined the permissions, so nothing was connected.",
}

export function LoginPage() {
  const [loading, setLoading] = useState(false)
  const errorCode = new URLSearchParams(window.location.search).get("login")
  const errorMsg = errorCode ? LOGIN_ERRORS[errorCode] || "Login failed. Try again." : null

  if (errorMsg) window.history.replaceState({}, "", "/")

  async function login() {
    setLoading(true)
    try {
      const { auth_url } = await post<{ auth_url: string }>("/api/auth/start")
      window.location.href = auth_url
    } catch {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Card className="w-full max-w-md p-6 space-y-4">
        {errorMsg && (
          <div className="rounded-md bg-destructive/15 px-4 py-3 text-sm text-destructive">
            {errorMsg}
          </div>
        )}
        <p className="text-muted-foreground text-sm">
          Log in with Spotify to start tracking. Nothing is counted until you do, and
          the only things written to your account are the playlists below.
        </p>
        <Button className="w-full" onClick={login} disabled={loading}>
          {loading ? "Redirecting..." : "Log in with Spotify"}
        </Button>
        <p className="text-muted-foreground text-xs">
          This runs in Spotify&apos;s Development Mode, which allows five authorised
          accounts. Your Spotify account email has to be on the app&apos;s allowlist.
        </p>
      </Card>
    </div>
  )
}
