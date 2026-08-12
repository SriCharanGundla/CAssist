import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"

import { DocumentPage } from "@/pages/document-page"
import * as api from "@/lib/api"

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal()
  return { ...original, getDocument: vi.fn(), getRun: vi.fn() }
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

    expect(await screen.findByText("Extraction complete")).toBeInTheDocument()
    expect(
      screen.getByText(
        "Extraction finished. The result still requires human review."
      )
    ).toBeInTheDocument()
    expect(await screen.findByText("2 of 2 pages complete")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Review extraction" })
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
  })
})
