import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"

import * as api from "@/lib/api"
import { ComparePage } from "@/pages/compare-page"

vi.mock("@/lib/api", () => ({
  compareDocument: vi.fn(),
  getDocument: vi.fn(),
}))

describe("ComparePage", () => {
  it("starts an explicit two-provider comparison and shows agreement", async () => {
    api.getDocument.mockResolvedValue({ original_filename: "invoice.pdf" })
    api.compareDocument.mockResolvedValue({
      runs: [
        {
          run_id: "run-gemini",
          provider: "gemini",
          model_id: "gemini-test",
          status: "succeeded",
          latency_ms: 120,
          input_tokens: 10,
          output_tokens: 5,
          quality_issue_count: 0,
          correction_count: 0,
        },
        {
          run_id: "run-openai",
          provider: "openai",
          model_id: "openai-test",
          status: "succeeded",
          latency_ms: 100,
          input_tokens: 8,
          output_tokens: 4,
          quality_issue_count: 1,
          correction_count: 0,
        },
      ],
      agreement: {
        matching_observations: 3,
        compared_observations: 4,
        match_rate: 0.75,
        difference_count: 2,
        differences: [
          {
            kind: "field",
            label: "Invoice number",
            value: "INV-1",
            gemini_count: 1,
            openai_count: 0,
          },
          {
            kind: "field",
            label: "Invoice number",
            value: "INV-I",
            gemini_count: 0,
            openai_count: 1,
          },
        ],
      },
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const user = userEvent.setup()

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/dev/compare/document-1"]}>
          <Routes>
            <Route path="/dev/compare/:documentId" element={<ComparePage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )

    await user.click(
      await screen.findByRole("button", { name: "Start comparison" })
    )
    expect(await screen.findByText("gemini-test")).toBeInTheDocument()
    expect(screen.getByText("openai-test")).toBeInTheDocument()
    expect(screen.getByText("75%")).toBeInTheDocument()
    expect(screen.getByText("Observation differences")).toBeInTheDocument()
    expect(screen.getByText("INV-1")).toBeInTheDocument()
    expect(screen.getByText("INV-I")).toBeInTheDocument()
    expect(api.compareDocument).toHaveBeenCalledWith("document-1")
  })
})
