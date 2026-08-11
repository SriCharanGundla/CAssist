import * as React from "react"

import { Button } from "@/components/ui/button"
import { getCurrentAuth, loginUrl, logout } from "@/lib/api"

export function App() {
  const [authState, setAuthState] = React.useState({
    status: "loading",
    data: null,
    error: null,
  })

  React.useEffect(() => {
    const controller = new AbortController()
    getCurrentAuth({ signal: controller.signal })
      .then((data) => {
        setAuthState({
          status: data ? "authenticated" : "anonymous",
          data,
          error: null,
        })
      })
      .catch((error) => {
        if (error.name !== "AbortError") {
          setAuthState({ status: "error", data: null, error: error.message })
        }
      })
    return () => controller.abort()
  }, [])

  const handleLogin = () => {
    const returnTo = `${window.location.pathname}${window.location.search}`
    window.location.assign(loginUrl(returnTo))
  }

  const handleLogout = async () => {
    setAuthState((current) => ({ ...current, status: "loading", error: null }))
    try {
      const { logout_url: providerLogoutUrl } = await logout()
      window.location.assign(providerLogoutUrl)
    } catch (error) {
      setAuthState((current) => ({
        ...current,
        status: "authenticated",
        error: error.message,
      }))
    }
  }

  return (
    <main className="flex min-h-svh items-center justify-center p-6">
      <section className="flex max-w-md min-w-0 flex-col gap-4 text-sm leading-loose">
        <div>
          <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
            CA document processing
          </p>
          <h1 className="mt-1 text-2xl font-semibold">CAssist</h1>
          <p className="mt-2 text-muted-foreground">
            Upload, extract, review, and export accounting documents.
          </p>
          {authState.status === "authenticated" ? (
            <div className="mt-4 flex flex-col items-start gap-3">
              <p className="text-muted-foreground">
                Signed in as {authState.data.user.email}
              </p>
              <div className="flex gap-2">
                <Button>Upload a document</Button>
                <Button variant="outline" onClick={handleLogout}>
                  Sign out
                </Button>
              </div>
            </div>
          ) : (
            <Button
              className="mt-4"
              disabled={authState.status === "loading"}
              onClick={handleLogin}
            >
              {authState.status === "loading" ? "Checking session…" : "Sign in"}
            </Button>
          )}
          {authState.error ? (
            <p className="mt-3 text-destructive" role="alert">
              {authState.error}
            </p>
          ) : null}
        </div>
        <div className="font-mono text-xs text-muted-foreground">
          (Press <kbd>d</kbd> to toggle dark mode)
        </div>
      </section>
    </main>
  )
}

export default App
