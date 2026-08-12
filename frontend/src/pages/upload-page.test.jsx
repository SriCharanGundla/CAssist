import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"

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
        <Route
          element={<p>Document status destination</p>}
          path="/documents/:documentId"
        />
      </Routes>
    </MemoryRouter>
  )
}

describe("UploadPage", () => {
  it("rejects unsupported files before calling the API", async () => {
    const { container } = renderUploadPage()

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: {
        files: [new File(["text"], "invoice.txt", { type: "text/plain" })],
      },
    })

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose a PDF, JPEG, or PNG file."
    )
    expect(api.uploadDocument).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Back" })).toHaveAttribute(
      "href",
      "/"
    )
  })

  it("navigates to the document returned after upload completion", async () => {
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

    expect(
      await screen.findByText("Document status destination")
    ).toBeInTheDocument()
    expect(api.uploadDocument).toHaveBeenCalledWith(
      expect.objectContaining({ name: "invoice.pdf" }),
      expect.objectContaining({ onStage: expect.any(Function) })
    )
  })
})
