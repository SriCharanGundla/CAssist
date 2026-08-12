import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as api from "@/lib/api"
import { ReviewPage } from "@/pages/review-page"

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal()
  return {
    ...original,
    getResult: vi.fn(),
    correctResult: vi.fn(),
    updateResultReview: vi.fn(),
  }
})

const initialResult = {
  result_id: "result-1",
  run_id: "run-1",
  document_type: "tax_invoice",
  version: 1,
  review_status: "unreviewed",
  reviewed_by_user_id: null,
  reviewed_at: null,
  canonical_data: {},
  effective_data: {
    document_type: "tax_invoice",
    invoice_number: "INV-1",
    invoice_date: "2026-08-12",
    due_date: null,
    currency: "INR",
    supplier: {
      name: "Supplier Ltd",
      gstin: "27ABCDE1234F1Z0",
      pan: "ABCDE1234F",
      address: null,
      state_code: "27",
    },
    buyer: {
      name: null,
      gstin: null,
      pan: null,
      address: null,
      state_code: null,
    },
    place_of_supply: "27",
    reverse_charge: false,
    line_items: [
      {
        description: "Professional services",
        hsn_sac: "9983",
        quantity: "1",
        unit: "NOS",
        unit_price: "100.00",
        discount: null,
        taxable_value: "100.00",
        gst_rate: "18",
        tax_amounts: { cgst: "9.00", sgst: "9.00", igst: null, cess: null },
        total: "118.00",
        source_pages: [1],
      },
    ],
    totals: {
      taxable_amount: "100.00",
      discount_amount: null,
      cgst_amount: "9.00",
      sgst_amount: "9.00",
      igst_amount: null,
      cess_amount: null,
      round_off: null,
      grand_total: "118.00",
    },
    notes: [],
  },
  validation_issues: [
    {
      severity: "warning",
      code: "MISSING_PARTY_NAME",
      field_path: "/buyer/name",
      message: "Buyer name was not extracted",
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
  })

  it("shows warnings beside effective accounting fields", async () => {
    renderReviewPage()

    expect(
      await screen.findByText("Review extracted invoice")
    ).toBeInTheDocument()
    expect(screen.getByText("1 validation warning")).toBeInTheDocument()
    expect(screen.getByText("Buyer name was not extracted")).toBeInTheDocument()
    expect(screen.getAllByText("118.00")).toHaveLength(2)
    expect(screen.getByText("Line items (1)")).toBeInTheDocument()
    expect(screen.getByText("Source pages: 1")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Approve extraction" })
    ).toBeEnabled()
  })

  it("saves an append-only field correction with the current version", async () => {
    const user = userEvent.setup()
    const corrected = structuredClone(initialResult)
    corrected.version = 2
    corrected.review_status = "in_review"
    corrected.effective_data.buyer.name = "Buyer Ltd"
    corrected.validation_issues = []
    corrected.corrections = [
      {
        id: "correction-1",
        field_path: "/buyer/name",
        previous_value: null,
        corrected_value: "Buyer Ltd",
        reason: "Checked against original",
      },
    ]
    api.correctResult.mockResolvedValue(corrected)
    renderReviewPage()
    await screen.findByText("Review extracted invoice")

    const buyerNameField = screen
      .getByText("Buyer name was not extracted")
      .closest(".border-b")
    await user.click(
      within(buyerNameField).getByRole("button", { name: "Edit" })
    )
    await user.type(within(buyerNameField).getByLabelText("Name"), "Buyer Ltd")
    await user.type(
      within(buyerNameField).getByLabelText("Reason for changing Name"),
      "Checked against original"
    )
    await user.click(
      within(buyerNameField).getByRole("button", { name: "Save" })
    )

    expect(api.correctResult).toHaveBeenCalledWith("result-1", 1, [
      {
        field_path: "/buyer/name",
        value: "Buyer Ltd",
        reason: "Checked against original",
      },
    ])
    expect(await screen.findByText("Version 2 · In review")).toBeInTheDocument()
    expect(screen.getByText("No validation warnings")).toBeInTheDocument()
  })

  it("records explicit human approval against the current version", async () => {
    const user = userEvent.setup()
    api.updateResultReview.mockResolvedValue({
      ...structuredClone(initialResult),
      version: 2,
      review_status: "approved",
      reviewed_by_user_id: "user-1",
      reviewed_at: "2026-08-12T12:00:00Z",
    })
    renderReviewPage()

    await user.click(
      await screen.findByRole("button", { name: "Approve extraction" })
    )

    expect(api.updateResultReview).toHaveBeenCalledWith(
      "result-1",
      1,
      "approved"
    )
    expect(await screen.findByText("Version 2 · Approved")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Return to review" })
    ).toBeEnabled()
  })
})
