import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as api from "@/lib/api"
import { SettingsPage } from "@/pages/settings-page"

vi.mock("@/lib/api", () => ({
  listAuthSessions: vi.fn(),
  revokeAuthSession: vi.fn(),
}))

const auth = {
  user: {
    display_name: "Alex Morgan",
    email: "owner@example.test",
  },
  workspaces: [{ id: "workspace-1", name: "My workspace", role: "owner" }],
}

function renderSettings() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsPage auth={auth} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listAuthSessions.mockImplementation(({ page }) =>
      Promise.resolve({
        items: [
          {
            id: `session-${page}`,
            device_label: page === 1 ? "Chrome on macOS" : "Safari on iPhone",
            created_at: "2026-08-13T08:00:00Z",
            last_seen_at: "2026-08-13T09:00:00Z",
            expires_at: "2026-08-27T08:00:00Z",
            is_current: page === 1,
          },
        ],
        page,
        page_size: 5,
        total: 6,
        total_pages: 2,
      })
    )
    api.revokeAuthSession.mockResolvedValue(undefined)
  })

  it("shows the profile, current device, and paginated sessions", async () => {
    const user = userEvent.setup()
    renderSettings()

    expect(
      screen.getByRole("heading", { name: "Settings" })
    ).toBeInTheDocument()
    expect(screen.getByText("Alex Morgan")).toBeInTheDocument()
    expect(await screen.findByText("Chrome on macOS")).toBeInTheDocument()
    expect(screen.getByText("Current")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Go to next page" }))
    expect(await screen.findByText("Safari on iPhone")).toBeInTheDocument()
    expect(api.listAuthSessions).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2, pageSize: 5 })
    )

    await user.click(
      screen.getByRole("button", { name: "Sign out Safari on iPhone" })
    )
    expect(api.revokeAuthSession.mock.calls[0][0]).toBe("session-2")
  })
})
