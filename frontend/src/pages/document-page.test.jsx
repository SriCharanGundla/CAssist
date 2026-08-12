import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { DocumentPage } from "@/pages/document-page"
import * as api from "@/lib/api"

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal()
  return {
    ...original,
    createOriginalViewUrl: vi.fn(),
    deleteDocumentOriginal: vi.fn(),
    getDocument: vi.fn(),
    getRun: vi.fn(),
    permanentlyDeleteDocument: vi.fn(),
    retryDocumentProcessing: vi.fn(),
  }
})

function renderDocumentPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/documents/document-1"]}>
        <Routes>
          <Route element={<DocumentPage />} path="/documents/:documentId" />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe("DocumentPage", () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it("polls the linked run and marks completed extraction as review-required", async () => {
    api.getDocument.mockResolvedValue({
      id: "document-1",
      original_filename: "invoice.pdf",
      mime_type: "application/pdf",
      status: "ready",
      original_available: true,
      latest_run: { id: "run-1", status: "succeeded" },
    })
    api.getRun.mockResolvedValue({
      id: "run-1",
      status: "succeeded",
      result_id: "result-1",
      error: null,
      progress: { completed_pages: 2, total_pages: 2 },
    })

    renderDocumentPage()

    expect(
      await screen.findByRole("heading", { name: "Extraction complete" })
    ).toBeInTheDocument()
    expect(
      screen.queryByText(
        "Extraction finished. The result still requires human review."
      )
    ).not.toBeInTheDocument()
    expect(screen.queryByText("2 of 2 pages complete")).not.toBeInTheDocument()
    expect(
      await screen.findByRole("button", { name: "Review" })
    ).toHaveAttribute("href", "/results/result-1/review")
  })

  it("shows only the backend-safe failure message", async () => {
    api.getDocument.mockResolvedValue({
      id: "document-1",
      original_filename: "invoice.pdf",
      mime_type: "application/pdf",
      status: "failed",
      original_available: true,
      latest_run: { id: "run-1", status: "failed" },
    })
    api.getRun.mockResolvedValue({
      id: "run-1",
      status: "failed",
      result_id: null,
      error: {
        code: "extraction_failed",
        message: "Extraction could not finish.",
      },
      progress: { completed_pages: null, total_pages: 2 },
    })

    renderDocumentPage()

    expect(
      await screen.findByText("Extraction could not finish.")
    ).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Extraction could not finish."
    )
    expect(
      screen.getByRole("button", { name: "Retry extraction" })
    ).toBeEnabled()
  })

  it("opens the private original from the filename", async () => {
    const user = userEvent.setup()
    const openWindow = vi.spyOn(window, "open").mockImplementation(() => null)
    api.getDocument.mockResolvedValue({
      id: "document-1",
      original_filename: "invoice.pdf",
      mime_type: "application/pdf",
      status: "ready",
      original_available: true,
      latest_run: { id: "run-1", status: "succeeded" },
    })
    api.getRun.mockResolvedValue({
      id: "run-1",
      status: "succeeded",
      result_id: "result-1",
      error: null,
      progress: { completed_pages: 1, total_pages: 1 },
    })
    api.createOriginalViewUrl.mockResolvedValue({
      url: "https://download.invalid/original?signature=test",
    })
    renderDocumentPage()

    await user.click(
      await screen.findByRole("button", {
        name: "Open invoice.pdf in a new tab",
      })
    )

    expect(api.createOriginalViewUrl).toHaveBeenCalledWith("document-1")
    expect(openWindow).toHaveBeenCalledWith(
      "https://download.invalid/original?signature=test",
      "_blank",
      "noopener,noreferrer"
    )
  })

  it("retries failed extraction without uploading the original again", async () => {
    const user = userEvent.setup()
    api.getDocument.mockResolvedValue({
      id: "document-1",
      original_filename: "invoice.pdf",
      mime_type: "application/pdf",
      status: "failed",
      original_available: true,
      latest_run: { id: "run-1", status: "failed" },
    })
    api.getRun.mockResolvedValue({
      id: "run-1",
      status: "failed",
      result_id: null,
      error: { code: "PROVIDER_RATE_LIMITED", message: "Provider was busy." },
      progress: { completed_pages: null, total_pages: 1 },
    })
    api.retryDocumentProcessing.mockResolvedValue({
      document_id: "document-1",
      run_id: "run-2",
      status: "uploaded",
    })
    renderDocumentPage()

    await user.click(
      await screen.findByRole("button", { name: "Retry extraction" })
    )

    expect(api.retryDocumentProcessing).toHaveBeenCalledWith("document-1")
  })

  it("shows a spinner and the current agent stage while processing", async () => {
    api.getDocument.mockResolvedValue({
      id: "document-1",
      original_filename: "invoice.png",
      mime_type: "image/png",
      status: "processing",
      original_available: true,
      latest_run: { id: "run-1", status: "extracting" },
    })
    api.getRun.mockResolvedValue({
      id: "run-1",
      status: "extracting",
      result_id: null,
      error: null,
      progress: {
        stage: "quality_check",
        completed_pages: null,
        total_pages: 1,
      },
    })

    renderDocumentPage()

    expect(
      await screen.findByRole("heading", { name: "Checking quality..." })
    ).toBeInTheDocument()
    expect(screen.queryByText("Processing status")).not.toBeInTheDocument()
    expect(screen.getByRole("status", { name: "Processing" })).toHaveClass(
      "animate-spin"
    )
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled()
    expect(
      screen.queryByRole("button", { name: "Upload another" })
    ).not.toBeInTheDocument()
  })

  it("offers file-only deletion and then removes the open-original action", async () => {
    const user = userEvent.setup()
    api.getDocument
      .mockResolvedValueOnce({
        id: "document-1",
        original_filename: "invoice.pdf",
        mime_type: "application/pdf",
        status: "ready",
        original_available: true,
        latest_run: { id: "run-1", status: "succeeded" },
      })
      .mockResolvedValue({
        id: "document-1",
        original_filename: "invoice.pdf",
        mime_type: "application/pdf",
        status: "ready",
        original_available: false,
        latest_run: { id: "run-1", status: "succeeded" },
      })
    api.getRun.mockResolvedValue({
      id: "run-1",
      status: "succeeded",
      result_id: "result-1",
      error: null,
      progress: { completed_pages: 1, total_pages: 1 },
    })
    api.deleteDocumentOriginal.mockResolvedValue(undefined)
    renderDocumentPage()

    expect(
      await screen.findByRole("button", {
        name: "Open invoice.pdf in a new tab",
      })
    ).toBeEnabled()
    await user.click(screen.getByRole("button", { name: "Delete" }))
    expect(api.deleteDocumentOriginal).not.toHaveBeenCalled()
    expect(
      screen.getByRole("dialog", { name: "Delete document?" })
    ).toBeInTheDocument()
    await user.click(
      screen.getByRole("button", { name: "Delete File, Keep Data" })
    )
    expect(api.deleteDocumentOriginal).toHaveBeenCalledWith("document-1")
    await waitFor(() =>
      expect(
        screen.queryByRole("button", {
          name: "Open invoice.pdf in a new tab",
        })
      ).not.toBeInTheDocument()
    )
  })

  it("offers deleting both the file and extraction data", async () => {
    const user = userEvent.setup()
    api.getDocument.mockResolvedValue({
      id: "document-1",
      original_filename: "invoice.pdf",
      mime_type: "application/pdf",
      status: "ready",
      original_available: true,
      latest_run: { id: "run-1", status: "succeeded" },
    })
    api.getRun.mockResolvedValue({
      id: "run-1",
      status: "succeeded",
      result_id: "result-1",
      error: null,
      progress: { completed_pages: 1, total_pages: 1 },
    })
    api.permanentlyDeleteDocument.mockResolvedValue(undefined)
    renderDocumentPage()

    await user.click(
      await screen.findByRole("button", {
        name: "Delete",
      })
    )
    expect(api.permanentlyDeleteDocument).not.toHaveBeenCalled()
    await user.click(
      screen.getByRole("button", { name: "Delete File and Data" })
    )

    await waitFor(() =>
      expect(api.permanentlyDeleteDocument).toHaveBeenCalledWith("document-1")
    )
  })

  it("shows only extraction deletion after the original file is gone", async () => {
    const user = userEvent.setup()
    api.getDocument.mockResolvedValue({
      id: "document-1",
      original_filename: "invoice.pdf",
      mime_type: "application/pdf",
      status: "ready",
      original_available: false,
      latest_run: { id: "run-1", status: "succeeded" },
    })
    api.getRun.mockResolvedValue({
      id: "run-1",
      status: "succeeded",
      result_id: "result-1",
      error: null,
      progress: { completed_pages: 1, total_pages: 1 },
    })
    renderDocumentPage()

    await user.click(await screen.findByRole("button", { name: "Delete" }))

    expect(
      screen.queryByRole("button", {
        name: "Open invoice.pdf in a new tab",
      })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Delete File, Keep Data" })
    ).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Delete Data" })).toBeEnabled()
  })
})
