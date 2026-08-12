import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { SourcePreview } from "@/components/source-preview"

const { destroyDocument, getDocument, renderPage } = vi.hoisted(() => ({
  destroyDocument: vi.fn(),
  getDocument: vi.fn(),
  renderPage: vi.fn(() => ({ cancel: vi.fn(), promise: Promise.resolve() })),
}))

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument,
}))

class VisiblePageObserver {
  constructor(callback) {
    this.callback = callback
  }

  observe() {
    this.callback([{ isIntersecting: true }])
  }

  disconnect() {}
}

describe("SourcePreview PDF viewer", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal("IntersectionObserver", VisiblePageObserver)
    getDocument.mockReturnValue({
      destroy: vi.fn(),
      promise: Promise.resolve({
        destroy: destroyDocument,
        getPage: vi.fn(async (pageNumber) => ({
          getViewport: ({ scale }) => ({
            height: 800 * scale,
            width: 600 * scale,
          }),
          pageNumber,
          render: renderPage,
        })),
        numPages: 2,
      }),
    })
  })

  it("loads pages through PDF.js and rerenders visible pages after zooming", async () => {
    const user = userEvent.setup()
    render(
      <SourcePreview
        mimeType="application/pdf"
        sourceUrl="https://download.invalid/original.pdf"
      />
    )

    expect(await screen.findByText("2 pages")).toBeInTheDocument()
    expect(getDocument).toHaveBeenCalledWith({
      url: "https://download.invalid/original.pdf",
    })
    expect(screen.getByLabelText("Page 1")).toBeInTheDocument()
    expect(screen.getByLabelText("Page 2")).toBeInTheDocument()
    await waitFor(() => expect(renderPage).toHaveBeenCalledTimes(2))

    await user.click(screen.getByRole("button", { name: "Zoom in" }))

    expect(screen.getByText("125%")).toBeInTheDocument()
    await waitFor(() => expect(renderPage).toHaveBeenCalledTimes(4))
  })

  it("offers the signed original for spreadsheet review", () => {
    render(
      <SourcePreview
        mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        sourceUrl="https://download.invalid/original.xlsx"
      />
    )

    expect(screen.getByRole("button", { name: /Open original/ })).toHaveAttribute(
      "href",
      "https://download.invalid/original.xlsx"
    )
    expect(screen.queryByAltText("Original document")).not.toBeInTheDocument()
  })
})
