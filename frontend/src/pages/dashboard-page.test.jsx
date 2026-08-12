import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as api from "@/lib/api"
import { DashboardPage } from "@/pages/dashboard-page"

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal()
  return { ...original, listDocuments: vi.fn() }
})

function renderDashboard(initialEntry = "/") {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={[initialEntry]}>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe("DashboardPage", () => {
  beforeEach(() => vi.resetAllMocks())

  it("shows recent documents and follows opaque pagination", async () => {
    const user = userEvent.setup()
    api.listDocuments
      .mockResolvedValueOnce({
        items: [
          {
            id: "document-1",
            original_filename: "invoice-one.pdf",
            status: "ready",
            created_at: "2026-08-12T12:00:00Z",
          },
        ],
        next_cursor: "opaque-cursor",
      })
      .mockResolvedValueOnce({
        items: [
          {
            id: "document-2",
            original_filename: "invoice-two.png",
            status: "processing",
            created_at: "2026-08-11T12:00:00Z",
          },
        ],
        next_cursor: null,
      })
    renderDashboard()

    expect(await screen.findByText("invoice-one.pdf")).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: /invoice-one.pdf/ })
    ).toHaveAttribute("href", "/documents/document-1")
    await user.click(screen.getByRole("button", { name: "Load more" }))
    expect(await screen.findByText("invoice-two.png")).toBeInTheDocument()
    expect(api.listDocuments).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "opaque-cursor", limit: 10 })
    )
  })

  it("shows an explicit empty state", async () => {
    api.listDocuments.mockResolvedValue({ items: [], next_cursor: null })
    renderDashboard()
    expect(await screen.findByText("No documents yet")).toBeInTheDocument()
  })
})
