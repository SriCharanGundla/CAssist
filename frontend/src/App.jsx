import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import {
  RiComputerLine,
  RiLogoutBoxRLine,
  RiMoonLine,
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
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { getCurrentAuth, loginUrl, logout } from "@/lib/api"
import { DashboardPage } from "@/pages/dashboard-page"
import { DocumentPage } from "@/pages/document-page"
import { ReviewPage } from "@/pages/review-page"
import { UploadPage } from "@/pages/upload-page"

function AuthenticatedApp({ auth }) {
  const { setTheme, theme } = useTheme()
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
  const ThemeIcon =
    theme === "dark"
      ? RiMoonLine
      : theme === "light"
        ? RiSunLine
        : RiComputerLine

  return (
    <div className="min-h-svh bg-muted/30">
      <header className="border-b bg-background">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-4">
          <Link className="text-lg font-semibold tracking-tight" to="/">
            CAssist
          </Link>
          <div className="flex min-w-0 items-center gap-3">
            <DropdownMenu>
              <DropdownMenuTrigger
                aria-label="Choose color theme"
                render={<Button size="icon" variant="ghost" />}
              >
                <ThemeIcon className="size-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-40">
                <DropdownMenuRadioGroup onValueChange={setTheme} value={theme}>
                  <DropdownMenuLabel>Theme</DropdownMenuLabel>
                  <DropdownMenuRadioItem value="light">
                    <RiSunLine /> Light
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="dark">
                    <RiMoonLine /> Dark
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="system">
                    <RiComputerLine /> System
                  </DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
            <DropdownMenu>
              <DropdownMenuTrigger
                aria-label="Open account menu"
                className="grid size-9 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground ring-offset-background outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
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
      <main className="mx-auto max-w-7xl px-5 py-8">
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
          Continue with an authorized Google account to upload, extract, review,
          and export accounting documents.
        </p>
        <Button className="mt-5" onClick={handleLogin} size="lg">
          Continue with Google
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
