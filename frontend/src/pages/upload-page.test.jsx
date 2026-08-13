import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as api from "@/lib/api"
import { UploadPage } from "@/pages/upload-page"

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal()
  return {
    ...original,
    getStorageQuota: vi.fn(),
    uploadDocument: vi.fn(),
  }
})

function renderUploadPage() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={["/upload"]}>
        <Routes>
          <Route element={<UploadPage />} path="/upload" />
          <Route element={<p>Dashboard destination</p>} path="/" />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe("UploadPage", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    api.getStorageQuota.mockResolvedValue({
      used_bytes: 1_000_000_000,
      limit_bytes: 8_000_000_000,
      available_bytes: 7_000_000_000,
      usage_percent: 12.5,
      upload_allowed: true,
    })
  })

  it("blocks file selection when shared storage is full", async () => {
    api.getStorageQuota.mockResolvedValue({
      used_bytes: 8_000_000_000,
      limit_bytes: 8_000_000_000,
      available_bytes: 0,
      usage_percent: 100,
      upload_allowed: false,
    })
    const { container } = renderUploadPage()

    expect(
      await screen.findByText(/Shared document storage is full/)
    ).toBeInTheDocument()
    expect(container.querySelector('input[type="file"]')).toBeDisabled()
    expect(screen.getByRole("button", { name: "Choose files" })).toBeDisabled()
  })

  it("blocks a selection larger than the remaining shared storage", async () => {
    const user = userEvent.setup()
    api.getStorageQuota.mockResolvedValue({
      used_bytes: 7_999_999_995,
      limit_bytes: 8_000_000_000,
      available_bytes: 5,
      usage_percent: 99.9999999,
      upload_allowed: true,
    })
    const { container } = renderUploadPage()

    await user.upload(
      container.querySelector('input[type="file"]'),
      new File(["%PDF-1.7"], "invoice.pdf", { type: "application/pdf" })
    )

    expect(
      screen.getByText(/exceed the remaining shared storage/)
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Upload and process" })
    ).toBeDisabled()
    expect(api.uploadDocument).not.toHaveBeenCalled()
  })

  it("rejects unsupported files before calling the API", async () => {
    const { container } = renderUploadPage()

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: {
        files: [new File(["text"], "invoice.txt", { type: "text/plain" })],
      },
    })

    expect(
      screen.getByText("Choose a PDF, JPEG, or PNG file.")
    ).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent(
      "1 invalid file was excluded"
    )
    expect(api.uploadDocument).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Back" })).toHaveAttribute(
      "href",
      "/"
    )
  })

  it("rejects selections larger than ten files", () => {
    const { container } = renderUploadPage()
    const files = Array.from(
      { length: 11 },
      (_, index) =>
        new File(["%PDF-1.7"], `invoice-${index}.pdf`, {
          type: "application/pdf",
        })
    )

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files },
    })

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose no more than 10 files at once."
    )
    expect(api.uploadDocument).not.toHaveBeenCalled()
  })

  it("keeps valid files when invalid files are selected with them", async () => {
    const { container } = renderUploadPage()
    await screen.findByText(/7\.00 GB available/)
    const valid = new File(["%PDF-1.7"], "invoice.pdf", {
      type: "application/pdf",
    })
    const invalid = new File(["text"], "notes.txt", { type: "text/plain" })

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [valid, invalid] },
    })

    expect(screen.getByText("invoice.pdf")).toBeInTheDocument()
    expect(screen.getByText("notes.txt")).toBeInTheDocument()
    expect(screen.getByText(/1 invalid file was excluded/)).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Upload and process" })
    ).toBeEnabled()
  })

  it("highlights the drop zone while files are dragged over it", () => {
    renderUploadPage()
    const dropZone = screen.getByLabelText("Document drop zone")

    fireEvent.dragEnter(dropZone)
    expect(dropZone).toHaveAttribute("data-drag-active", "true")

    fireEvent.dragLeave(dropZone)
    expect(dropZone).not.toHaveAttribute("data-drag-active")
  })

  it("returns to the dashboard after upload completion", async () => {
    const user = userEvent.setup()
    api.uploadDocument.mockImplementation(async (_file, { onStage }) => {
      onStage("uploading")
      return { document_id: "document-1", status: "uploaded" }
    })
    const { container } = renderUploadPage()

    await user.upload(
      container.querySelector('input[type="file"]'),
      new File(["%PDF-1.7"], "invoice.pdf", { type: "application/pdf" })
    )
    await user.click(screen.getByRole("button", { name: "Upload and process" }))

    expect(await screen.findByText("Dashboard destination")).toBeInTheDocument()
    expect(api.uploadDocument).toHaveBeenCalledWith(
      expect.objectContaining({ name: "invoice.pdf" }),
      expect.objectContaining({ onStage: expect.any(Function) })
    )
  })

  it("uploads multiple selected files and reports each stage", async () => {
    const user = userEvent.setup()
    api.uploadDocument.mockImplementation(async (file, { onStage }) => {
      onStage("uploading")
      onStage("verifying")
      return {
        document_id: file.name,
        status: "uploaded",
        deduplicated: false,
      }
    })
    const { container } = renderUploadPage()
    const files = [
      new File(["%PDF-1.7"], "invoice.pdf", { type: "application/pdf" }),
      new File(["image"], "receipt.png", { type: "image/png" }),
    ]

    await user.upload(container.querySelector('input[type="file"]'), files)
    await user.click(
      screen.getByRole("button", { name: "Upload and process 2 files" })
    )

    expect(await screen.findByText("Dashboard destination")).toBeInTheDocument()
    expect(api.uploadDocument).toHaveBeenCalledTimes(2)
    expect(api.uploadDocument).toHaveBeenCalledWith(
      expect.objectContaining({ name: "invoice.pdf" }),
      expect.objectContaining({ onStage: expect.any(Function) })
    )
    expect(api.uploadDocument).toHaveBeenCalledWith(
      expect.objectContaining({ name: "receipt.png" }),
      expect.objectContaining({ onStage: expect.any(Function) })
    )
  })

  it("limits batch uploads to three active files", async () => {
    const user = userEvent.setup()
    let active = 0
    let maximumActive = 0
    const releases = []
    api.uploadDocument.mockImplementation(
      (file) =>
        new Promise((resolve) => {
          active += 1
          maximumActive = Math.max(maximumActive, active)
          releases.push(() => {
            active -= 1
            resolve({
              document_id: file.name,
              status: "uploaded",
              deduplicated: false,
            })
          })
        })
    )
    const { container } = renderUploadPage()
    const files = Array.from(
      { length: 5 },
      (_, index) =>
        new File(["%PDF-1.7"], `invoice-${index}.pdf`, {
          type: "application/pdf",
        })
    )

    await user.upload(container.querySelector('input[type="file"]'), files)
    await user.click(
      screen.getByRole("button", { name: "Upload and process 5 files" })
    )

    expect(api.uploadDocument).toHaveBeenCalledTimes(3)
    releases.splice(0, 3).forEach((release) => release())
    await vi.waitFor(() => expect(api.uploadDocument).toHaveBeenCalledTimes(5))
    releases.splice(0).forEach((release) => release())
    expect(await screen.findByText("Dashboard destination")).toBeInTheDocument()
    expect(maximumActive).toBe(3)
  })

  it("retries only failed files after a partial batch failure", async () => {
    const user = userEvent.setup()
    let failedOnce = false
    api.uploadDocument.mockImplementation(async (file, { onStage }) => {
      onStage("uploading")
      if (file.name === "second.pdf" && !failedOnce) {
        failedOnce = true
        throw new Error("Storage unavailable")
      }
      return {
        document_id: file.name,
        status: "uploaded",
        deduplicated: false,
      }
    })
    const { container } = renderUploadPage()

    await user.upload(container.querySelector('input[type="file"]'), [
      new File(["%PDF-1.7"], "first.pdf", { type: "application/pdf" }),
      new File(["%PDF-1.7"], "second.pdf", { type: "application/pdf" }),
    ])
    await user.click(
      screen.getByRole("button", { name: "Upload and process 2 files" })
    )

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "1 of 2 files failed"
    )
    await user.click(
      screen.getByRole("button", { name: "Retry 1 failed file" })
    )

    expect(await screen.findByText("Dashboard destination")).toBeInTheDocument()
    expect(api.uploadDocument).toHaveBeenCalledTimes(3)
  })

  it("shows upload progress and cancels an in-flight file", async () => {
    const user = userEvent.setup()
    api.uploadDocument.mockImplementation(
      (_file, { onProgress, onStage, signal }) =>
        new Promise((_resolve, reject) => {
          onStage("uploading")
          onProgress(42)
          signal.addEventListener("abort", () =>
            reject(new DOMException("Upload cancelled", "AbortError"))
          )
        })
    )
    const { container } = renderUploadPage()
    await user.upload(
      container.querySelector('input[type="file"]'),
      new File(["%PDF-1.7"], "invoice.pdf", { type: "application/pdf" })
    )
    await user.click(screen.getByRole("button", { name: "Upload and process" }))

    expect(
      await screen.findByRole("progressbar", {
        name: "invoice.pdf upload progress",
      })
    ).toHaveAttribute("aria-valuenow", "42")
    await user.click(
      screen.getByRole("button", { name: "Cancel upload invoice.pdf" })
    )

    expect(await screen.findByText("Upload cancelled")).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent(
      "1 upload was cancelled"
    )
  })
})
