import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { SourcePreview } from "@/components/source-preview"

const { destroyDocument, getDocument, renderPage } = vi.hoisted(() => ({
  destroyDocument: vi.fn(),
  getDocument: vi.fn(),
  renderPage: vi.fn(() => ({ cancel: vi.fn(), promise: Promise.resolve() })),
}))

const { getSpreadsheetPreview } = vi.hoisted(() => ({
  getSpreadsheetPreview: vi.fn(),
}))

vi.mock("@/lib/api", () => ({ getSpreadsheetPreview }))

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument,
}))

class VisiblePageObserver {
  constructor(callback) {
    this.callback = callback
  }

  observe(target) {
    this.callback([{ intersectionRatio: 1, isIntersecting: true, target }])
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
          getViewport: ({ rotation = 0, scale }) =>
            rotation % 180 === 0
              ? { height: 800 * scale, width: 600 * scale }
              : { height: 600 * scale, width: 800 * scale },
          pageNumber,
          render: renderPage,
        })),
        numPages: 2,
      }),
    })
    getSpreadsheetPreview.mockResolvedValue({
      sheets: [
        {
          name: "Invoices",
          rows: [
            ["Invoice", "Amount"],
            ["INV-1", "118.00"],
          ],
        },
      ],
      truncated: false,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("loads pages through PDF.js and rerenders visible pages after zooming", async () => {
    const user = userEvent.setup()
    render(
      <SourcePreview
        mimeType="application/pdf"
        sourceUrl="https://download.invalid/original.pdf"
      />
    )

    expect(await screen.findByText("1 / 2")).toBeInTheDocument()
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

  it("navigates and rotates pages with compact viewer controls", async () => {
    const user = userEvent.setup()
    render(
      <SourcePreview
        mimeType="application/pdf"
        sourceUrl="https://download.invalid/original.pdf"
      />
    )

    const viewer = await screen.findByLabelText("PDF document viewer")
    viewer.scrollTo = vi.fn()
    expect(await screen.findByText("1 / 2")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Next page" }))

    expect(screen.getByText("2 / 2")).toBeInTheDocument()
    expect(viewer.scrollTo).toHaveBeenCalledWith({ behavior: "smooth", top: 0 })

    await user.click(screen.getByRole("button", { name: "Rotate clockwise" }))

    expect(screen.getByLabelText("Page 1")).toHaveStyle({
      height: "600px",
      width: "800px",
    })
    await waitFor(() => expect(renderPage).toHaveBeenCalledTimes(4))
    expect(screen.getByRole("button", { name: "Fit page" })).toBeEnabled()
  })

  it("explains viewer icons with visible tooltips", async () => {
    render(
      <SourcePreview
        mimeType="application/pdf"
        sourceUrl="https://download.invalid/original.pdf"
      />
    )

    const zoomIn = await screen.findByRole("button", { name: "Zoom in" })
    fireEvent.focus(zoomIn)
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Zoom in")
  })

  it("tracks pointer movement directly without a delayed pan frame", async () => {
    render(
      <SourcePreview
        mimeType="application/pdf"
        sourceUrl="https://download.invalid/original.pdf"
      />
    )

    const viewer = await screen.findByLabelText("PDF document viewer")
    viewer.setPointerCapture = vi.fn()
    viewer.hasPointerCapture = vi.fn(() => true)
    viewer.releasePointerCapture = vi.fn()
    Object.defineProperty(viewer, "scrollLeft", { value: 40, writable: true })
    Object.defineProperty(viewer, "scrollTop", { value: 30, writable: true })

    fireEvent.pointerDown(viewer, {
      button: 0,
      clientX: 100,
      clientY: 100,
      pointerId: 1,
    })
    fireEvent.pointerMove(viewer, { clientX: 80, clientY: 80, pointerId: 1 })
    fireEvent.pointerMove(viewer, { clientX: 70, clientY: 60, pointerId: 1 })

    expect(viewer.scrollLeft).toBe(70)
    expect(viewer.scrollTop).toBe(70)

    fireEvent.pointerUp(viewer, { pointerId: 1 })
    expect(viewer.releasePointerCapture).toHaveBeenCalledWith(1)
  })

  it("zooms the focused PDF viewer with keyboard shortcuts", async () => {
    render(
      <SourcePreview
        mimeType="application/pdf"
        sourceUrl="https://download.invalid/original.pdf"
      />
    )

    const viewer = await screen.findByLabelText("PDF document viewer")
    fireEvent.keyDown(viewer, { key: "+" })
    expect(screen.getByText("125%")).toBeInTheDocument()

    fireEvent.keyDown(viewer, { key: "-" })
    expect(screen.getByText("100%")).toBeInTheDocument()
  })

  it("previews spreadsheet cells inline and keeps the signed original action", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <SourcePreview
          documentId="document-1"
          mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          sourceUrl="https://download.invalid/original.xlsx"
        />
      </QueryClientProvider>
    )

    expect(await screen.findByText("INV-1")).toBeInTheDocument()
    expect(getSpreadsheetPreview).toHaveBeenCalledWith("document-1", {
      signal: expect.any(AbortSignal),
    })
    expect(
      screen.getByRole("button", { name: /Open original/ })
    ).toHaveAttribute("href", "https://download.invalid/original.xlsx")
    expect(screen.queryByAltText("Original document")).not.toBeInTheDocument()
  })

  it("keeps a zoomed image partially inside its viewport", async () => {
    const user = userEvent.setup()
    render(
      <SourcePreview
        mimeType="image/png"
        sourceUrl="https://download.invalid/original.png"
      />
    )

    const viewport = screen.getByLabelText("Image document viewer")
    const image = screen.getByAltText("Original document")
    Object.defineProperties(viewport, {
      clientHeight: { configurable: true, value: 300 },
      clientWidth: { configurable: true, value: 400 },
    })
    Object.defineProperties(image, {
      offsetHeight: { configurable: true, value: 200 },
      offsetWidth: { configurable: true, value: 200 },
    })
    viewport.setPointerCapture = vi.fn()

    await user.click(screen.getByRole("button", { name: "Zoom in" }))
    fireEvent.pointerDown(viewport, {
      clientX: 0,
      clientY: 0,
      pointerId: 1,
    })
    fireEvent.pointerMove(viewport, {
      clientX: 1_000,
      clientY: 1_000,
      pointerId: 1,
    })

    expect(image).toHaveStyle({
      transform: "translate(277px, 227px) scale(1.25)",
    })
  })

  it("uses the same maximum zoom for images and PDFs", async () => {
    const user = userEvent.setup()
    render(
      <SourcePreview
        mimeType="image/png"
        sourceUrl="https://download.invalid/original.png"
      />
    )

    const zoomIn = screen.getByRole("button", { name: "Zoom in" })
    for (let count = 0; count < 8; count += 1) await user.click(zoomIn)

    expect(screen.getByText("300%")).toBeInTheDocument()
    expect(zoomIn).toBeDisabled()
  })

  it("offers a retry action when loading the signed original fails", async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    render(
      <SourcePreview
        error={new Error("Unable to load original")}
        mimeType="image/png"
        onRetry={onRetry}
      />
    )

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Unable to load original"
    )
    await user.click(screen.getByRole("button", { name: "Retry" }))
    expect(onRetry).toHaveBeenCalledOnce()
  })
})
