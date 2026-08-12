import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { getCurrentAuth, loginUrl, logout } from "@/lib/api"
import { DashboardPage } from "@/pages/dashboard-page"
import { DocumentPage } from "@/pages/document-page"
import { ReviewPage } from "@/pages/review-page"
import { UploadPage } from "@/pages/upload-page"

function AuthenticatedApp({ auth }) {
  const [logoutError, setLogoutError] = React.useState(null)
  const [isLoggingOut, setIsLoggingOut] = React.useState(false)

  const handleLogout = async () => {
    setIsLoggingOut(true)
    setLogoutError(null)
    try {
      const { logout_url: providerLogoutUrl } = await logout()
      window.location.assign(providerLogoutUrl)
    } catch (error) {
      setLogoutError(error.message)
      setIsLoggingOut(false)
    }
  }

  return (
    <div className="min-h-svh bg-muted/30">
      <header className="border-b bg-background">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-5 py-4">
          <Link className="text-lg font-semibold tracking-tight" to="/">
            CAssist
          </Link>
          <div className="flex min-w-0 items-center gap-3">
            <span className="hidden truncate text-xs text-muted-foreground sm:block">
              {auth.user.email}
            </span>
            <Button
              disabled={isLoggingOut}
              onClick={handleLogout}
              variant="outline"
            >
              {isLoggingOut ? "Signing out…" : "Sign out"}
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-5 py-8">
        {logoutError ? (
          <p className="mb-4 text-sm text-destructive" role="alert">
            {logoutError}
          </p>
        ) : null}
        <Routes>
          <Route element={<DashboardPage />} path="/" />
          <Route element={<UploadPage />} path="/upload" />
          <Route element={<DocumentPage />} path="/documents/:documentId" />
          <Route element={<ReviewPage />} path="/results/:resultId/review" />
          <Route element={<Navigate replace to="/" />} path="*" />
        </Routes>
      </main>
      <footer className="mx-auto max-w-5xl px-5 pb-6 font-mono text-xs text-muted-foreground">
        Press <kbd>d</kbd> to toggle dark mode
      </footer>
    </div>
  )
}

export function App() {
  const location = useLocation()
  const authQuery = useQuery({
    queryKey: ["auth"],
    queryFn: ({ signal }) => getCurrentAuth({ signal }),
    retry: false,
  })

  const handleLogin = () => {
    window.location.assign(loginUrl(`${location.pathname}${location.search}`))
  }

  if (authQuery.isPending) {
    return (
      <main className="grid min-h-svh place-items-center p-6 text-sm text-muted-foreground">
        Checking your session…
      </main>
    )
  }

  if (authQuery.data) {
    return <AuthenticatedApp auth={authQuery.data} />
  }

  return (
    <main className="grid min-h-svh place-items-center p-6">
      <section className="w-full max-w-md rounded-2xl border bg-card p-6 shadow-sm">
        <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
          CA document processing
        </p>
        <h1 className="mt-2 text-3xl font-semibold">CAssist</h1>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          Sign in to upload, extract, review, and export accounting documents.
        </p>
        <Button className="mt-5" onClick={handleLogin} size="lg">
          Sign in
        </Button>
        {authQuery.error ? (
          <p className="mt-4 text-sm text-destructive" role="alert">
            {authQuery.error.message}
          </p>
        ) : null}
      </section>
    </main>
  )
}

export default App
