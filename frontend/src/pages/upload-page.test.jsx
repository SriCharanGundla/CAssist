import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as api from "@/lib/api"
import { UploadPage } from "@/pages/upload-page"

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal()
  return { ...original, uploadDocument: vi.fn() }
})

function renderUploadPage() {
  return render(
    <MemoryRouter initialEntries={["/upload"]}>
      <Routes>
        <Route element={<UploadPage />} path="/upload" />
        <Route element={<p>Dashboard destination</p>} path="/" />
      </Routes>
    </MemoryRouter>
  )
}

describe("UploadPage", () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it("rejects unsupported files before calling the API", async () => {
    const { container } = renderUploadPage()

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: {
        files: [new File(["text"], "invoice.txt", { type: "text/plain" })],
      },
    })

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose a PDF, JPEG, PNG, CSV, or XLSX file."
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
      new File(["Invoice,Amount"], "invoices.csv", { type: "text/csv" }),
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
      expect.objectContaining({ name: "invoices.csv" }),
      expect.objectContaining({ onStage: expect.any(Function) })
    )
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
})
