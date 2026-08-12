import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as api from "@/lib/api"
import { ReviewPage } from "@/pages/review-page"

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal()),
  getResult: vi.fn(),
  createOriginalViewUrl: vi.fn(),
  correctResult: vi.fn(),
  downloadTallyExport: vi.fn(),
  updateResultReview: vi.fn(),
}))

const extraction = {
  document_type: "tax_invoice",
  fields: [
    {
      id: "field-0001",
      label: "Bill No.",
      value: "INV-1",
      page_number: 1,
      region: null,
    },
    {
      id: "field-0002",
      label: "Grand Total",
      value: "118.00",
      page_number: 1,
      region: null,
    },
  ],
  tables: [
    {
      id: "table-0001",
      title: "Items",
      headers: ["Description", "Amount"],
      page_numbers: [1],
      rows: [
        {
          id: "table-0001-row-0001",
          cells: [
            { id: "table-0001-r0001-c0001", value: "Professional services" },
            { id: "table-0001-r0001-c0002", value: "118.00" },
          ],
        },
      ],
    },
  ],
  text_blocks: [
    { id: "text-0001", text: "Thank you", page_number: 1, region: null },
  ],
}

const initialResult = {
  result_id: "result-1",
  run_id: "run-1",
  document_id: "document-1",
  original_mime_type: "image/png",
  original_available: true,
  document_type: "tax_invoice",
  version: 1,
  review_status: "unreviewed",
  reviewed_by_user_id: null,
  reviewed_at: null,
  extracted_data: structuredClone(extraction),
  effective_data: structuredClone(extraction),
  presentation: {
    sections: [
      {
        id: "section-0001",
        title: "Invoice details",
        target_ids: ["field-0001", "field-0002"],
      },
      {
        id: "section-0002",
        title: "Items",
        target_ids: ["table-0001"],
      },
      {
        id: "section-0003",
        title: "Terms",
        target_ids: ["text-0001"],
      },
    ],
  },
  quality_issues: [
    {
      target_id: "field-0001",
      code: "possible_ocr_error",
      message: "Possible character confusion",
      suggested_value: "INV-7",
    },
  ],
  corrections: [],
}

function renderReviewPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/results/result-1/review"]}>
        <Routes>
          <Route element={<ReviewPage />} path="/results/:resultId/review" />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe("ReviewPage", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    api.getResult.mockResolvedValue(structuredClone(initialResult))
    api.createOriginalViewUrl.mockResolvedValue({
      url: "https://download.invalid/original?signature=test",
    })
  })

  it("renders only extracted generic fields, tables, and text", async () => {
    renderReviewPage()

    expect(
      await screen.findByText("Review extracted document")
    ).toBeInTheDocument()
    expect(screen.getByText("Bill No.")).toBeInTheDocument()
    expect(screen.getByText("Invoice details")).toBeInTheDocument()
    expect(screen.getByText("Items")).toBeInTheDocument()
    expect(screen.getByText("Professional services")).toBeInTheDocument()
    expect(screen.queryByText("Description, row 1")).not.toBeInTheDocument()
    expect(screen.getByText("Thank you")).toBeInTheDocument()
    expect(screen.getByText("Terms")).toBeInTheDocument()
    expect(screen.queryByText("Fields")).not.toBeInTheDocument()
    expect(screen.queryByText("Other text")).not.toBeInTheDocument()
    expect(screen.queryByText("Correction history")).not.toBeInTheDocument()
    expect(screen.getByText("Possible character confusion")).toBeInTheDocument()
    expect(screen.getByAltText("Original document")).toBeInTheDocument()
    expect(screen.queryByText("Supplier GSTIN")).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Approve" })
    ).toBeEnabled()
  })

  it("toggles the inline original preview", async () => {
    const user = userEvent.setup()
    renderReviewPage()

    expect(await screen.findByAltText("Original document")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Hide original" }))
    expect(screen.queryByAltText("Original document")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Show original" })).toBeEnabled()
  })

  it("copies a value by clicking it and shows brief feedback", async () => {
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, "writeText")
    renderReviewPage()

    await user.click(
      await screen.findByRole("button", { name: "Copy Bill No.: INV-1" })
    )

    expect(writeText).toHaveBeenCalledWith("INV-1")
    const copiedFeedback = screen.getByText("Copied")
    expect(copiedFeedback).toBeInTheDocument()
    expect(copiedFeedback.parentElement?.querySelector("svg")).toBeInTheDocument()
    expect(screen.queryByText("Ready for your review")).not.toBeInTheDocument()
    expect(screen.queryByText("Human review required")).not.toBeInTheDocument()
  })

  it("saves an append-only correction by stable target id", async () => {
    const user = userEvent.setup()
    const corrected = structuredClone(initialResult)
    corrected.version = 2
    corrected.review_status = "in_review"
    corrected.effective_data.fields[0].value = "INV-7"
    corrected.quality_issues = []
    corrected.corrections = [
      {
        id: "correction-1",
        target_id: "field-0001",
        previous_value: "INV-1",
        corrected_value: "INV-7",
        reason: "Accepted quality-review suggestion",
      },
    ]
    api.correctResult.mockResolvedValue(corrected)
    renderReviewPage()

    const issue = await screen.findByText("Possible character confusion")
    await user.click(
      within(issue.closest("li")).getByRole("button", { name: "Use “INV-7”" })
    )

    expect(api.correctResult).toHaveBeenCalledWith("result-1", 1, [
      {
        target_id: "field-0001",
        value: "INV-7",
        reason: "Accepted quality-review suggestion",
      },
    ])
    expect(await screen.findByText("Version 2 · In review")).toBeInTheDocument()
    expect(screen.getByText("Edited")).toBeInTheDocument()
    expect(screen.getByText("Changes (1)")).toBeInTheDocument()
  })

  it("records approval and exports only an approved result", async () => {
    const user = userEvent.setup()
    api.updateResultReview.mockResolvedValue({
      ...structuredClone(initialResult),
      version: 2,
      review_status: "approved",
      reviewed_by_user_id: "user-1",
      reviewed_at: "2026-08-12T12:00:00Z",
    })
    api.downloadTallyExport.mockResolvedValue(undefined)
    renderReviewPage()

    await user.click(
      await screen.findByRole("button", { name: "Approve" })
    )
    await user.click(
      await screen.findByRole("button", { name: "Download Tally JSON" })
    )

    expect(api.updateResultReview).toHaveBeenCalledWith(
      "result-1",
      1,
      "approved"
    )
    expect(api.downloadTallyExport).toHaveBeenCalledWith("result-1", 2)
  })

  it("keeps approved results read-only until they return to review", async () => {
    const user = userEvent.setup()
    const approved = {
      ...structuredClone(initialResult),
      version: 2,
      review_status: "approved",
      reviewed_by_user_id: "user-1",
      reviewed_at: "2026-08-12T12:00:00Z",
    }
    api.getResult.mockResolvedValue(approved)
    api.updateResultReview.mockResolvedValue({
      ...approved,
      version: 3,
      review_status: "in_review",
      reviewed_by_user_id: null,
      reviewed_at: null,
    })
    renderReviewPage()

    expect(
      await screen.findByRole("button", { name: "Return to review" })
    ).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Use “INV-7”" })
    ).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Return to review" }))

    expect(await screen.findByText("Version 3 · In review")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Edit" })).not.toHaveLength(0)
    expect(
      screen.getByRole("button", { name: "Use “INV-7”" })
    ).toBeInTheDocument()
  })
})
