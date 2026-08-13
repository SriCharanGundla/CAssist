import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import {
  RiLogoutBoxRLine,
  RiMoonLine,
  RiSettingsLine,
  RiSunLine,
} from "@remixicon/react"
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom"

import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { getCurrentAuth, loginUrl, logout } from "@/lib/api"
import { DashboardPage } from "@/pages/dashboard-page"
import { ComparePage } from "@/pages/compare-page"
import { ReviewPage } from "@/pages/review-page"
import { SettingsPage } from "@/pages/settings-page"
import { UploadPage } from "@/pages/upload-page"

function AuthenticatedApp({ auth }) {
  const location = useLocation()
  const { resolvedTheme, setTheme } = useTheme()
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

  const displayName = auth.user.display_name || auth.user.email
  const initials = displayName
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase()
  const toggleTheme = () => {
    const nextTheme = document.documentElement.classList.contains("dark")
      ? "light"
      : "dark"
    setTheme(nextTheme)
  }
  React.useEffect(() => {
    if (location.pathname.startsWith("/results/")) return
    const routeTitle =
      location.pathname === "/upload"
        ? "Upload"
        : location.pathname === "/settings"
          ? "Settings"
          : location.pathname.startsWith("/dev/compare/")
            ? "Compare models"
            : "Documents"
    document.title = `${routeTitle} — CAssist`
  }, [location.pathname])

  return (
    <div className="min-h-svh bg-muted/30">
      <a
        className="fixed top-2 left-2 z-[100] -translate-y-16 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground shadow focus:translate-y-0"
        href="#main-content"
      >
        Skip to content
      </a>
      <header className="border-b bg-background">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-4">
          <Link className="text-lg font-semibold tracking-tight" to="/">
            CAssist
          </Link>
          <div className="flex min-w-0 items-center gap-3">
            <Button
              aria-label="Toggle color theme"
              aria-pressed={resolvedTheme === "dark"}
              className="relative size-9 rounded-full"
              onClick={toggleTheme}
              size="icon"
              variant="ghost"
            >
              <RiSunLine className="size-[1.2rem] scale-100 rotate-0 transition-all dark:scale-0 dark:-rotate-90" />
              <RiMoonLine className="absolute size-[1.2rem] scale-0 rotate-90 transition-all dark:scale-100 dark:rotate-0" />
              <span className="sr-only">Toggle color theme</span>
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger
                aria-label="Open account menu"
                className="grid size-7 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground ring-offset-background outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 [@media(pointer:coarse)]:size-11"
              >
                {initials}
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuGroup>
                  <DropdownMenuLabel className="space-y-0.5 py-2">
                    <span className="block truncate font-medium text-foreground">
                      {displayName}
                    </span>
                    <span className="block truncate font-normal">
                      {auth.user.email}
                    </span>
                  </DropdownMenuLabel>
                </DropdownMenuGroup>
                <DropdownMenuSeparator />
                <DropdownMenuItem render={<Link to="/settings" />}>
                  <RiSettingsLine /> Settings
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  disabled={isLoggingOut}
                  onClick={handleLogout}
                  variant="destructive"
                >
                  <RiLogoutBoxRLine />
                  {isLoggingOut ? "Signing out…" : "Sign out"}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>
      <main
        className="mx-auto max-w-7xl px-5 py-8"
        id="main-content"
        tabIndex={-1}
      >
        {logoutError ? (
          <p className="mb-4 text-sm text-destructive" role="alert">
            {logoutError}
          </p>
        ) : null}
        <Routes>
          <Route element={<DashboardPage />} path="/" />
          <Route element={<UploadPage />} path="/upload" />
          <Route
            element={<Navigate replace to="/" />}
            path="/documents/:documentId"
          />
          <Route element={<ReviewPage />} path="/results/:resultId/review" />
          <Route element={<SettingsPage auth={auth} />} path="/settings" />
          {import.meta.env.DEV ? (
            <Route element={<ComparePage />} path="/dev/compare/:documentId" />
          ) : null}
          <Route element={<Navigate replace to="/" />} path="*" />
        </Routes>
      </main>
    </div>
  )
}

export function App() {
  const location = useLocation()
  const authQuery = useQuery({
    queryKey: ["auth"],
    queryFn: ({ signal }) => getCurrentAuth({ signal }),
    retry: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
    staleTime: 60 * 60 * 1_000,
  })

  const handleLogin = () => {
    window.location.assign(loginUrl(`${location.pathname}${location.search}`))
  }

  if (authQuery.isPending) {
    return (
      <main className="grid min-h-svh place-items-center p-6">
        <div className="text-center">
          <p className="text-lg font-semibold tracking-tight text-foreground">
            CAssist
          </p>
          <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
            <span
              aria-hidden="true"
              className="size-4 animate-spin rounded-full border-2 border-current/30 border-t-current"
            />
            Checking your session…
          </p>
        </div>
      </main>
    )
  }

  if (authQuery.data) {
    return <AuthenticatedApp auth={authQuery.data} />
  }

  if (authQuery.isError) {
    return (
      <main className="grid min-h-svh place-items-center p-6">
        <section className="w-full max-w-md rounded-2xl border bg-card p-6 shadow-sm">
          <h1 className="text-3xl font-semibold">CAssist</h1>
          <p className="mt-4 text-sm text-destructive" role="alert">
            {authQuery.error.message}
          </p>
          <Button
            className="mt-5"
            disabled={authQuery.isFetching}
            onClick={() => authQuery.refetch()}
            size="lg"
          >
            {authQuery.isFetching ? "Checking…" : "Retry"}
          </Button>
        </section>
      </main>
    )
  }

  return (
    <main className="grid min-h-svh place-items-center p-6">
      <section className="w-full max-w-md rounded-2xl border bg-card p-6 shadow-sm">
        <h1 className="text-3xl font-semibold">CAssist</h1>
        <Button className="mt-5" onClick={handleLogin} size="lg">
          Sign In with Google
        </Button>
      </section>
    </main>
  )
}

export default App
