import { beforeEach, describe, expect, it, vi } from "vitest"

import { API_BASE_URL, uploadDocument } from "@/lib/api"

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
