import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
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
    cancelProcessingRun: vi.fn(),
    confirmDocumentProcessing: vi.fn(),
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
            status: "ready",
            created_at: "2026-08-11T12:00:00Z",
            latest_run: {
              id: "run-2",
              status: "succeeded",
              result_id: "result-2",
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
    expect(screen.getByText("1")).toHaveAttribute("aria-current", "page")
    await user.click(screen.getByRole("button", { name: "Go to next page" }))
    expect(await screen.findByText("invoice-two.png")).toBeInTheDocument()
    await new Promise((resolve) => window.setTimeout(resolve, 300))
    expect(screen.getByText("invoice-two.png")).toBeInTheDocument()
    expect(screen.queryByText("invoice-one.pdf")).not.toBeInTheDocument()
    expect(screen.getByText("2")).toHaveAttribute("aria-current", "page")
    expect(api.listDocuments).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "opaque-cursor", limit: 10 })
    )
    await user.click(
      screen.getByRole("button", { name: "Go to previous page" })
    )
    expect(await screen.findByText("invoice-one.pdf")).toBeInTheDocument()
    expect(screen.queryByText("invoice-two.png")).not.toBeInTheDocument()
  })

  it("updates one processing row without refetching the dashboard page", async () => {
    api.listDocuments.mockResolvedValue({
      items: [
        {
          id: "document-1",
          original_filename: "invoice.pdf",
          mime_type: "application/pdf",
          original_available: true,
          status: "processing",
          created_at: "2026-08-12T12:00:00Z",
          latest_run: {
            id: "run-1",
            status: "extracting",
            result_id: null,
          },
        },
      ],
      next_cursor: null,
    })
    api.getRun.mockResolvedValue({
      id: "run-1",
      status: "succeeded",
      result_id: "result-1",
      progress: { stage: "complete" },
    })

    renderDashboard()

    expect(await screen.findByText("Extraction complete")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Review extraction" })
    ).toHaveAttribute("href", "/results/result-1/review")
    expect(
      screen.getByRole("button", { name: "Delete document" })
    ).toBeEnabled()
    expect(api.listDocuments).toHaveBeenCalledTimes(1)
    expect(api.getRun).toHaveBeenCalledWith(
      "run-1",
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    )
  })

  it("confirms only uncertain documents through a dialog", async () => {
    const user = userEvent.setup()
    api.listDocuments.mockResolvedValue({
      items: [
        {
          id: "document-uncertain",
          original_filename: "unclear.pdf",
          mime_type: "application/pdf",
          original_available: true,
          status: "needs_confirmation",
          created_at: "2026-08-13T12:00:00Z",
          latest_run: {
            id: "run-uncertain",
            status: "needs_confirmation",
            classification_scope: "uncertain",
            classification_reason_code: "insufficient_visible_content",
            result_id: null,
          },
        },
      ],
      next_cursor: null,
    })
    api.confirmDocumentProcessing.mockResolvedValue({
      document_id: "document-uncertain",
      run_id: "run-confirmed",
      status: "uploaded",
    })
    renderDashboard()

    expect(await screen.findByText("unclear.pdf")).toBeInTheDocument()
    expect(screen.getAllByText("Confirmation needed")).not.toHaveLength(0)
    await user.click(
      screen.getByRole("button", { name: "Confirm document processing" })
    )
    expect(
      screen.getByRole("heading", { name: "Process this document anyway?" })
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Process anyway" }))

    await waitFor(() =>
      expect(api.confirmDocumentProcessing).toHaveBeenCalledWith(
        "document-uncertain"
      )
    )
    expect(toast.success).toHaveBeenCalledWith(
      "Document confirmed and queued for extraction."
    )
  })

  it("hard-blocks unsupported documents without a confirmation action", async () => {
    api.listDocuments.mockResolvedValue({
      items: [
        {
          id: "document-unrelated",
          original_filename: "holiday-photo.png",
          mime_type: "image/png",
          original_available: true,
          status: "unsupported",
          created_at: "2026-08-13T12:00:00Z",
          latest_run: {
            id: "run-unrelated",
            status: "unsupported",
            classification_scope: "unrelated",
            classification_reason_code: "unrelated_content",
            result_id: null,
          },
        },
      ],
      next_cursor: null,
    })
    renderDashboard()

    expect(await screen.findByText("holiday-photo.png")).toBeInTheDocument()
    expect(screen.getAllByText("Unsupported document")).not.toHaveLength(0)
    expect(
      screen.queryByRole("button", { name: "Confirm document processing" })
    ).not.toBeInTheDocument()
  })

  it("requests cancellation for an active extraction", async () => {
    const user = userEvent.setup()
    api.listDocuments.mockResolvedValue({
      items: [
        {
          id: "document-1",
          original_filename: "invoice.pdf",
          mime_type: "application/pdf",
          original_available: true,
          status: "processing",
          created_at: "2026-08-12T12:00:00Z",
          latest_run: {
            id: "run-1",
            status: "extracting",
            result_id: null,
            cancellation_requested_at: null,
          },
        },
      ],
      next_cursor: null,
    })
    api.getRun.mockReturnValue(new Promise(() => {}))
    api.cancelProcessingRun.mockResolvedValue({
      run_id: "run-1",
      status: "stopping",
    })
    renderDashboard()

    const stopButton = await screen.findByRole("button", {
      name: "Stop processing",
    })
    expect(stopButton).toHaveClass("text-destructive")
    await user.click(stopButton)

    await waitFor(() =>
      expect(api.cancelProcessingRun).toHaveBeenCalledWith("run-1")
    )
    expect(
      screen.getByRole("button", { name: "Delete document" })
    ).toBeDisabled()
  })

  it("shows an explicit empty state", async () => {
    api.listDocuments.mockResolvedValue({ items: [], next_cursor: null })
    renderDashboard()
    expect(await screen.findByText("No documents yet")).toBeInTheDocument()
  })

  it("searches and filters documents through the paginated API", async () => {
    const user = userEvent.setup()
    api.listDocuments.mockResolvedValue({ items: [], next_cursor: null })
    renderDashboard()
    await screen.findByText("No documents yet")

    const search = screen.getByRole("searchbox", { name: "Search documents" })
    expect(
      search.compareDocumentPosition(screen.getByText("Actions")) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    await user.type(search, "acme")
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Filter by status" }),
      "ready"
    )
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Filter by document type" }),
      "tax_invoice"
    )

    await waitFor(() =>
      expect(api.listDocuments).toHaveBeenLastCalledWith(
        expect.objectContaining({
          cursor: null,
          documentType: "tax_invoice",
          limit: 10,
          search: "acme",
          status: "ready",
        })
      )
    )
    expect(screen.getByText("No matching documents")).toBeInTheDocument()
  })

  it("shows a structural loading state while documents load", () => {
    api.listDocuments.mockReturnValue(new Promise(() => {}))
    renderDashboard()
    expect(screen.getByLabelText("Loading documents")).toBeInTheDocument()
    expect(screen.queryByText("Loading documents…")).not.toBeInTheDocument()
  })

  it("retries a failed document-list query", async () => {
    const user = userEvent.setup()
    api.listDocuments
      .mockRejectedValueOnce(new Error("Unable to load documents"))
      .mockResolvedValueOnce({ items: [], next_cursor: null })
    renderDashboard()

    expect(
      await screen.findByText("Unable to load documents")
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Retry" }))
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
