import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { toast } from "sonner"

import * as api from "@/lib/api"
import { ReviewPage } from "@/pages/review-page"

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal()),
  getResult: vi.fn(),
  createOriginalViewUrl: vi.fn(),
  correctResult: vi.fn(),
  downloadTallyExport: vi.fn(),
  updateResultReview: vi.fn(),
  updateTallySelection: vi.fn(),
}))

vi.mock("sonner", () => ({
  toast: { info: vi.fn() },
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
  original_filename: "invoice.png",
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
    excluded_target_ids: [],
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
    localStorage.removeItem("cassist-review-source-visible")
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
    await waitFor(() =>
      expect(document.title).toBe("Review — invoice.png — CAssist")
    )
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
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled()
    expect(
      screen.getByRole("region", { name: "Invoice details content" })
    ).toHaveClass("max-h-[36rem]", "overflow-y-auto", "overscroll-contain")
  })

  it("toggles the inline original preview", async () => {
    const user = userEvent.setup()
    renderReviewPage()

    expect(await screen.findByAltText("Original document")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Hide document" }))
    expect(screen.queryByAltText("Original document")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Show document" })).toBeEnabled()
    expect(localStorage.getItem("cassist-review-source-visible")).toBe("false")
  })

  it("keeps extracted data visible without original controls after file deletion", async () => {
    const result = structuredClone(initialResult)
    result.original_available = false
    api.getResult.mockResolvedValue(result)
    renderReviewPage()

    expect(await screen.findByText("Bill No.")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Hide document" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Show document" })
    ).not.toBeInTheDocument()
    expect(screen.queryByAltText("Original document")).not.toBeInTheDocument()
    expect(api.createOriginalViewUrl).not.toHaveBeenCalled()
  })

  it("selects sections and individual items for Tally JSON", async () => {
    const user = userEvent.setup()
    const saved = structuredClone(initialResult)
    saved.version = 2
    saved.review_status = "in_review"
    saved.presentation.excluded_target_ids = ["field-0001", "field-0002"]
    api.updateTallySelection.mockResolvedValue(saved)
    renderReviewPage()

    const sectionSelection = await screen.findByRole("checkbox", {
      name: "Include Invoice details section in Tally JSON",
    })
    expect(sectionSelection).toBeChecked()
    await user.click(sectionSelection)

    expect(
      screen.getByRole("heading", { name: "Tally JSON content" }).parentElement
    ).toHaveTextContent("2 of 4 items selected.")
    expect(
      screen.getByText("Save this selection before approval.")
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled()
    await user.click(
      screen.getByRole("button", { name: "Save Tally selection" })
    )

    expect(api.updateTallySelection).toHaveBeenCalledWith("result-1", 1, [
      "field-0001",
      "field-0002",
    ])
    expect(await screen.findByText("Version 2 · In review")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled()

    await user.click(
      screen.getByRole("checkbox", {
        name: "Include Bill No. in Tally JSON",
      })
    )
    expect(
      screen.getByRole("heading", { name: "Tally JSON content" }).parentElement
    ).toHaveTextContent("3 of 4 items selected.")
  })

  it("highlights field evidence in the source preview", async () => {
    const result = structuredClone(initialResult)
    result.extracted_data.fields[0].region = {
      x: 100,
      y: 120,
      width: 200,
      height: 40,
    }
    result.effective_data.fields[0].region = {
      x: 100,
      y: 120,
      width: 200,
      height: 40,
    }
    api.getResult.mockResolvedValue(result)
    renderReviewPage()

    const image = await screen.findByAltText("Original document")
    Object.defineProperties(image, {
      naturalHeight: { configurable: true, value: 1_000 },
      naturalWidth: { configurable: true, value: 1_000 },
    })
    fireEvent.load(image)
    fireEvent.mouseEnter(
      screen.getByText("Bill No.").closest("[data-review-target]")
    )

    expect(
      await screen.findByLabelText("Highlighted source region")
    ).toHaveStyle({ left: "10%", top: "12%", width: "20%", height: "4%" })
  })

  it("collapses sections and moves between field editors with Tab", async () => {
    const user = userEvent.setup()
    renderReviewPage()

    const collapse = await screen.findByRole("button", {
      name: "Collapse Invoice details section",
    })
    await user.click(collapse)
    expect(screen.queryByText("Bill No.")).not.toBeInTheDocument()
    await user.click(
      screen.getByRole("button", { name: "Expand Invoice details section" })
    )

    await user.click(
      (await screen.findAllByRole("button", { name: "Edit" }))[0]
    )
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Bill No." }), {
      key: "Tab",
    })
    expect(
      await screen.findByRole("textbox", { name: "Grand Total" })
    ).toBeInTheDocument()
  })

  it("remembers when the original preview was hidden", async () => {
    const user = userEvent.setup()
    const firstRender = renderReviewPage()
    await screen.findByAltText("Original document")
    await user.click(screen.getByRole("button", { name: "Hide document" }))
    firstRender.unmount()

    renderReviewPage()

    expect(
      await screen.findByRole("button", { name: "Show document" })
    ).toBeEnabled()
    expect(screen.queryByAltText("Original document")).not.toBeInTheDocument()
  })

  it("grows the correction editor with its content", async () => {
    const user = userEvent.setup()
    renderReviewPage()
    const editButtons = await screen.findAllByRole("button", { name: "Edit" })
    await user.click(editButtons[0])
    const editor = screen.getByRole("textbox", { name: "Bill No." })
    Object.defineProperty(editor, "scrollHeight", {
      configurable: true,
      value: 180,
    })

    await user.type(editor, " with a longer corrected value")

    expect(editor).toHaveStyle({ height: "180px", overflowY: "hidden" })
  })

  it("saves and cancels correction editing with keyboard shortcuts", async () => {
    const user = userEvent.setup()
    api.correctResult.mockResolvedValue(structuredClone(initialResult))
    renderReviewPage()
    const editButtons = await screen.findAllByRole("button", { name: "Edit" })

    await user.click(editButtons[0])
    const editor = screen.getByRole("textbox", { name: "Bill No." })
    await user.clear(editor)
    await user.type(editor, "INV-9")
    fireEvent.keyDown(editor, { ctrlKey: true, key: "Enter" })

    await waitFor(() =>
      expect(api.correctResult).toHaveBeenCalledWith("result-1", 1, [
        { target_id: "field-0001", value: "INV-9", reason: null },
      ])
    )

    await user.click(
      (await screen.findAllByRole("button", { name: "Edit" }))[0]
    )
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Bill No." }), {
      key: "Escape",
    })
    expect(
      screen.queryByRole("textbox", { name: "Bill No." })
    ).not.toBeInTheDocument()
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
    expect(
      copiedFeedback.parentElement?.querySelector("svg")
    ).toBeInTheDocument()
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
    expect(screen.getByText("Original:")).toBeInTheDocument()
    expect(screen.getAllByText("INV-1")).toHaveLength(2)
    expect(screen.getByText("Changes (1)")).toBeInTheDocument()
    expect(screen.getByText("Before")).toBeInTheDocument()
    expect(screen.getByText("After")).toBeInTheDocument()
  })

  it("separates document-level quality issues from field issues", async () => {
    const result = structuredClone(initialResult)
    result.quality_issues.push({
      target_id: "document",
      code: "totals_do_not_reconcile",
      message: "Document totals need review",
      suggested_value: null,
    })
    api.getResult.mockResolvedValue(result)

    renderReviewPage()

    const alerts = await screen.findByRole("region", {
      name: "Document quality issues",
    })
    expect(
      within(alerts).getByText("Document-level issues")
    ).toBeInTheDocument()
    expect(
      within(alerts).getByText("Document totals need review")
    ).toBeInTheDocument()
  })

  it("retries a failed extraction-result query", async () => {
    const user = userEvent.setup()
    api.getResult
      .mockRejectedValueOnce(new Error("Unable to load extraction"))
      .mockResolvedValueOnce(structuredClone(initialResult))

    renderReviewPage()

    expect(
      await screen.findByText("Unable to load extraction")
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Retry" }))
    expect(
      await screen.findByText("Review extracted document")
    ).toBeInTheDocument()
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

    await user.click(await screen.findByRole("button", { name: "Approve" }))
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
    approved.presentation.excluded_target_ids = ["field-0001", "text-0001"]
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
    expect(
      screen.queryByRole("button", { name: "Edit" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Use “INV-7”" })
    ).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("heading", { name: "Tally JSON content" })
    ).not.toBeInTheDocument()
    expect(screen.queryByText("Bill No.")).not.toBeInTheDocument()
    expect(screen.getByText("Grand Total")).toBeInTheDocument()
    expect(screen.queryByText("Terms")).not.toBeInTheDocument()
    expect(screen.getByText("Items")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Return to review" }))

    expect(await screen.findByText("Version 3 · In review")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Edit" })).not.toHaveLength(0)
    expect(
      screen.getByRole("button", { name: "Use “INV-7”" })
    ).toBeInTheDocument()
    expect(screen.getByText("Bill No.")).toBeInTheDocument()
    expect(screen.getByText("Terms")).toBeInTheDocument()
    expect(screen.getAllByRole("checkbox")).toHaveLength(7)
  })

  it("loads the latest result with clear feedback after a version conflict", async () => {
    const user = userEvent.setup()
    const conflict = Object.assign(new Error("Result changed; reload"), {
      status: 409,
    })
    api.correctResult.mockRejectedValue(conflict)
    renderReviewPage()

    const issue = await screen.findByText("Possible character confusion")
    await user.click(
      within(issue.closest("li")).getByRole("button", { name: "Use “INV-7”" })
    )

    expect(toast.info).toHaveBeenCalledWith(
      "A newer version was loaded. Review your change and try again."
    )
    expect(screen.queryByText("Result changed; reload")).not.toBeInTheDocument()
  })
})
