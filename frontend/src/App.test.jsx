import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen } from "@testing-library/react"
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

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
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

  it("opens theme and account menus without breaking Base UI group context", async () => {
    const user = userEvent.setup()
    renderApp()

    const themeButton = await screen.findByRole("button", {
      name: "Choose color theme",
    })
    themeButton.focus()
    fireEvent.keyDown(themeButton, { key: "ArrowDown" })
    expect(await screen.findByText("Theme")).toBeInTheDocument()
    await user.click(screen.getByRole("menuitemradio", { name: "Dark" }))
    expect(document.documentElement).toHaveClass("dark")

    const accountButton = screen.getByRole("button", {
      name: "Open account menu",
    })
    accountButton.focus()
    fireEvent.keyDown(accountButton, { key: "ArrowDown" })
    expect(await screen.findByText("Alex Morgan")).toBeInTheDocument()
    expect(screen.getByText("owner@example.test")).toBeInTheDocument()
  })
})
