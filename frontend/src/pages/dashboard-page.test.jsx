import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { toast } from "sonner"

import * as api from "@/lib/api"
import { DashboardPage } from "@/pages/dashboard-page"

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal()
  return {
    ...original,
    createOriginalViewUrl: vi.fn(),
    deleteDocumentOriginal: vi.fn(),
    getRun: vi.fn(),
    listDocuments: vi.fn(),
    permanentlyDeleteDocument: vi.fn(),
    retryDocumentProcessing: vi.fn(),
  }
})

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}))

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

  it.each([
    [
      { uploaded: true },
      "success",
      "Document uploaded and queued for extraction.",
    ],
    [
      { deduplicated: true },
      "info",
      "Existing document and processing history reused.",
    ],
    [
      { uploaded: true, uploadCount: 3 },
      "success",
      "3 documents uploaded and queued for extraction.",
    ],
  ])("shows route feedback as a typed toast", async (state, type, message) => {
    api.listDocuments.mockResolvedValue({ items: [], next_cursor: null })

    renderDashboard({ pathname: "/", state })

    expect(await screen.findByText("No documents yet")).toBeInTheDocument()
    expect(toast[type]).toHaveBeenCalledWith(message)
    expect(screen.queryByText(message)).not.toBeInTheDocument()
  })

  it("shows recent documents and follows opaque pagination", async () => {
    const user = userEvent.setup()
    api.listDocuments
      .mockResolvedValueOnce({
        items: [
          {
            id: "document-1",
            original_filename: "invoice-one.pdf",
            mime_type: "application/pdf",
            original_available: true,
            status: "ready",
            created_at: "2026-08-12T12:00:00Z",
            latest_run: {
              id: "run-1",
              status: "succeeded",
              result_id: "result-1",
            },
          },
        ],
        next_cursor: "opaque-cursor",
      })
      .mockResolvedValueOnce({
        items: [
          {
            id: "document-2",
            original_filename: "invoice-two.png",
            mime_type: "image/png",
            original_available: true,
            status: "processing",
            created_at: "2026-08-11T12:00:00Z",
            latest_run: {
              id: "run-2",
              status: "extracting",
              result_id: null,
            },
          },
        ],
        next_cursor: null,
      })
    renderDashboard()

    expect(await screen.findByText("invoice-one.pdf")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /open invoice-one.pdf/i })
    ).toBeEnabled()
    expect(screen.getByText("PDF")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Review extraction" })
    ).toHaveAttribute("href", "/results/result-1/review")
    const deleteButton = screen.getByRole("button", {
      name: "Delete document",
    })
    expect(deleteButton).toBeEnabled()
    expect(deleteButton).toHaveClass("text-destructive")
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

  it("opens the original and offers both deletion choices", async () => {
    const user = userEvent.setup()
    const open = vi.spyOn(window, "open").mockImplementation(() => null)
    api.createOriginalViewUrl.mockResolvedValue({
      url: "https://download.invalid/invoice",
    })
    api.listDocuments.mockResolvedValue({
      items: [
        {
          id: "document-1",
          original_filename: "invoice.pdf",
          mime_type: "application/pdf",
          original_available: true,
          status: "ready",
          created_at: "2026-08-12T12:00:00Z",
          latest_run: {
            id: "run-1",
            status: "succeeded",
            result_id: "result-1",
          },
        },
      ],
      next_cursor: null,
    })
    renderDashboard()

    await user.click(
      await screen.findByRole("button", {
        name: "Open invoice.pdf in a new tab",
      })
    )
    expect(open).toHaveBeenCalledWith(
      "https://download.invalid/invoice",
      "_blank",
      "noopener,noreferrer"
    )

    await user.click(screen.getByRole("button", { name: "Delete document" }))
    expect(
      screen.getByRole("button", { name: "Delete File, Keep Data" })
    ).toBeEnabled()
    expect(
      screen.getByRole("button", { name: "Delete File and Data" })
    ).toBeEnabled()
    open.mockRestore()
  })
})
