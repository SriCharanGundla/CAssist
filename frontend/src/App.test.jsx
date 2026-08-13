import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { App } from "@/App"
import { ThemeProvider } from "@/components/theme-provider"
import * as api from "@/lib/api"

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal()),
  getCurrentAuth: vi.fn(),
  listDocuments: vi.fn(),
  logout: vi.fn(),
}))

function renderApp(initialEntries = ["/"]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>
  )
}

describe("authenticated app header", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    localStorage.clear()
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({
        addEventListener: vi.fn(),
        matches: false,
        removeEventListener: vi.fn(),
      })),
    })
    api.getCurrentAuth.mockResolvedValue({
      user: {
        display_name: "Alex Morgan",
        email: "owner@example.test",
      },
    })
    api.listDocuments.mockResolvedValue({ items: [], next_cursor: null })
  })

  it("toggles and remembers an explicit color theme", async () => {
    const user = userEvent.setup()
    renderApp()

    const themeButton = await screen.findByRole("button", {
      name: "Toggle color theme",
    })
    expect(document.documentElement).toHaveClass("light")
    expect(themeButton).toHaveAttribute("aria-pressed", "false")
    expect(localStorage.getItem("cassist-theme")).toBeNull()

    await user.click(themeButton)
    expect(document.documentElement).toHaveClass("dark")
    expect(themeButton).toHaveAttribute("aria-pressed", "true")
    expect(localStorage.getItem("cassist-theme")).toBe("dark")

    await user.click(themeButton)
    expect(document.documentElement).toHaveClass("light")
    expect(themeButton).toHaveAttribute("aria-pressed", "false")
    expect(localStorage.getItem("cassist-theme")).toBe("light")
  })

  it("opens the account menu without breaking Base UI group context", async () => {
    renderApp()

    const accountButton = await screen.findByRole("button", {
      name: "Open account menu",
    })
    accountButton.focus()
    fireEvent.keyDown(accountButton, { key: "ArrowDown" })
    expect(await screen.findByText("Alex Morgan")).toBeInTheDocument()
    expect(screen.getByText("owner@example.test")).toBeInTheDocument()
  })

  it("provides skip navigation and route-aware browser titles", async () => {
    renderApp(["/upload"])

    const skipLink = await screen.findByRole("link", {
      name: "Skip to content",
    })
    expect(skipLink).toHaveAttribute("href", "#main-content")
    expect(document.getElementById("main-content")).toHaveAttribute(
      "tabindex",
      "-1"
    )
    await waitFor(() => expect(document.title).toBe("Upload — CAssist"))
  })

  it("expands shared button and account targets on coarse pointers", async () => {
    renderApp()

    expect(
      await screen.findByRole("button", { name: "Toggle color theme" })
    ).toHaveClass("[@media(pointer:coarse)]:min-h-11")
    expect(
      screen.getByRole("button", { name: "Open account menu" })
    ).toHaveClass("[@media(pointer:coarse)]:size-11")
  })

  it("shows branded progress while checking the session", async () => {
    api.getCurrentAuth.mockReturnValue(new Promise(() => {}))
    const view = renderApp()

    expect(await screen.findByText("CAssist")).toBeInTheDocument()
    expect(screen.getByText("Checking your session…")).toBeInTheDocument()
    expect(view.container.querySelector(".animate-spin")).toBeInTheDocument()
  })

  it("shows the concise Google sign-in screen", async () => {
    api.getCurrentAuth.mockResolvedValue(null)
    renderApp()

    expect(
      await screen.findByRole("button", { name: "Sign In with Google" })
    ).toBeEnabled()
    expect(screen.queryByText("CA document processing")).not.toBeInTheDocument()
    expect(
      screen.queryByText(/Continue with an authorized Google account/)
    ).not.toBeInTheDocument()
  })

  it("lets the user retry a failed session check", async () => {
    const user = userEvent.setup()
    api.getCurrentAuth
      .mockRejectedValueOnce(new Error("The request timed out. Try again."))
      .mockResolvedValueOnce({
        user: {
          display_name: "Alex Morgan",
          email: "owner@example.test",
        },
      })
    renderApp()

    expect(
      await screen.findByText("The request timed out. Try again.")
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Retry" }))

    expect(
      await screen.findByRole("button", { name: "Open account menu" })
    ).toBeInTheDocument()
    expect(api.getCurrentAuth).toHaveBeenCalledTimes(2)
  })
})
