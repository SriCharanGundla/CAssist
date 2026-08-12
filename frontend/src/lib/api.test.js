import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  API_BASE_URL,
  correctResult,
  deleteDocumentOriginal,
  permanentlyDeleteDocument,
  uploadDocument,
} from "@/lib/api"

function jsonResponse(payload, init = {}) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  })
}

describe("uploadDocument", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("creates, uploads, verifies, and completes a private upload", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "create-csrf" }))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            document_id: "document-1",
            upload: {
              method: "PUT",
              url: "https://storage.example/upload",
              headers: { "Content-Type": "application/pdf" },
            },
          },
          { status: 201 }
        )
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "complete-csrf" }))
      .mockResolvedValueOnce(
        jsonResponse(
          { document_id: "document-1", status: "uploaded" },
          { status: 202 }
        )
      )
    const stages = []
    const file = new File(["%PDF-1.7"], "invoice.pdf", {
      type: "application/pdf",
    })

    const result = await uploadDocument(file, {
      onStage: (stage) => stages.push(stage),
    })

    expect(result).toEqual({ document_id: "document-1", status: "uploaded" })
    expect(stages).toEqual(["creating", "uploading", "verifying"])
    expect(fetchMock).toHaveBeenCalledTimes(5)
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `${API_BASE_URL}/uploads`,
      expect.objectContaining({
        credentials: "include",
        method: "POST",
        body: JSON.stringify({
          filename: "invoice.pdf",
          mime_type: "application/pdf",
          byte_size: file.size,
        }),
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "X-CSRF-Token": "create-csrf",
        }),
      })
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "https://storage.example/upload",
      {
        method: "PUT",
        headers: { "Content-Type": "application/pdf" },
        body: file,
      }
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      `${API_BASE_URL}/uploads/document-1/complete`,
      expect.objectContaining({
        credentials: "include",
        method: "POST",
        headers: expect.objectContaining({
          "X-CSRF-Token": "complete-csrf",
        }),
      })
    )
  })

  it("stops before completion when private storage rejects the upload", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf" }))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            document_id: "document-1",
            upload: {
              method: "PUT",
              url: "https://storage.example/upload",
              headers: { "Content-Type": "application/pdf" },
            },
          },
          { status: 201 }
        )
      )
      .mockResolvedValueOnce(new Response(null, { status: 403 }))

    await expect(
      uploadDocument(
        new File(["%PDF-1.7"], "invoice.pdf", { type: "application/pdf" })
      )
    ).rejects.toThrow("The file could not be uploaded to private storage.")
    expect(fetch).toHaveBeenCalledTimes(3)
  })
})

describe("correctResult", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("uses a fresh CSRF token and preserves decimal values as strings", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "correction-csrf" }))
      .mockResolvedValueOnce(
        jsonResponse({ result_id: "result-1", version: 2 })
      )

    await correctResult("result-1", 1, [
      {
        field_path: "/totals/grand_total",
        value: "1180.00",
        reason: "Checked against invoice",
      },
    ])

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `${API_BASE_URL}/results/result-1/fields`,
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          expected_version: 1,
          changes: [
            {
              field_path: "/totals/grand_total",
              value: "1180.00",
              reason: "Checked against invoice",
            },
          ],
        }),
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "X-CSRF-Token": "correction-csrf",
        }),
      })
    )
  })
})

describe("document deletion", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it.each([
    [deleteDocumentOriginal, "/documents/document-1/original"],
    [permanentlyDeleteDocument, "/documents/document-1"],
  ])("sends a CSRF-protected DELETE to the intended endpoint", async (remove, path) => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "delete-csrf" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    await remove("document-1")

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `${API_BASE_URL}${path}`,
      expect.objectContaining({
        credentials: "include",
        method: "DELETE",
        headers: expect.objectContaining({
          "X-CSRF-Token": "delete-csrf",
        }),
      })
    )
  })
})
