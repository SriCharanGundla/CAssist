import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  API_BASE_URL,
  correctResult,
  deleteDocumentOriginal,
  getCurrentAuth,
  permanentlyDeleteDocument,
  retryDocumentProcessing,
  uploadDocument,
  uploadMimeType,
} from "@/lib/api"

function jsonResponse(payload, init = {}) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  })
}

let xhrStatus = 200
const xhrRequests = []

class FakeXMLHttpRequest extends EventTarget {
  constructor() {
    super()
    this.headers = {}
    this.status = 0
    this.upload = new EventTarget()
    xhrRequests.push(this)
  }

  abort() {
    this.dispatchEvent(new Event("abort"))
  }

  open(method, url) {
    this.method = method
    this.url = url
  }

  send(body) {
    this.body = body
    this.upload.dispatchEvent(
      new ProgressEvent("progress", {
        lengthComputable: true,
        loaded: body.size,
        total: body.size,
      })
    )
    this.status = xhrStatus
    queueMicrotask(() => this.dispatchEvent(new Event("load")))
  }

  setRequestHeader(name, value) {
    this.headers[name] = value
  }
}

describe("uploadDocument", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    xhrRequests.length = 0
    xhrStatus = 200
    vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest)
  })

  it("normalizes image MIME types from safe filename extensions", () => {
    expect(uploadMimeType(new File(["image"], "receipt.jpeg"))).toBe(
      "image/jpeg"
    )
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
    expect(fetchMock).toHaveBeenCalledTimes(4)
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
          "Idempotency-Key": expect.any(String),
          "X-CSRF-Token": "create-csrf",
        }),
      })
    )
    expect(xhrRequests).toHaveLength(1)
    expect(xhrRequests[0]).toMatchObject({
      body: file,
      headers: { "Content-Type": "application/pdf" },
      method: "PUT",
      url: "https://storage.example/upload",
    })
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      `${API_BASE_URL}/uploads/document-1/complete`,
      expect.objectContaining({
        credentials: "include",
        method: "POST",
        headers: expect.objectContaining({
          "Idempotency-Key": expect.any(String),
          "X-CSRF-Token": "complete-csrf",
        }),
      })
    )
  })

  it("stops before completion when private storage rejects the upload", async () => {
    xhrStatus = 403
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

    await expect(
      uploadDocument(
        new File(["%PDF-1.7"], "invoice.pdf", { type: "application/pdf" })
      )
    ).rejects.toThrow("The file could not be uploaded to private storage.")
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it("reports byte progress and aborts an in-flight storage upload", async () => {
    const controller = new AbortController()
    const progress = []
    vi.spyOn(FakeXMLHttpRequest.prototype, "send").mockImplementation(
      function send(body) {
        this.body = body
        this.upload.dispatchEvent(
          new ProgressEvent("progress", {
            lengthComputable: true,
            loaded: 4,
            total: 10,
          })
        )
      }
    )
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

    const upload = uploadDocument(
      new File(["%PDF-1.7"], "invoice.pdf", { type: "application/pdf" }),
      { onProgress: (value) => progress.push(value), signal: controller.signal }
    )
    await vi.waitFor(() => expect(progress).toContain(40))
    controller.abort()

    await expect(upload).rejects.toMatchObject({ name: "AbortError" })
  })
})

describe("API request timeout", () => {
  it("aborts a standard API request after thirty seconds", async () => {
    vi.useFakeTimers()
    try {
      vi.spyOn(globalThis, "fetch").mockImplementation(
        (_url, { signal }) =>
          new Promise((_resolve, reject) => {
            signal.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError"))
            )
          })
      )

      const request = expect(getCurrentAuth()).rejects.toThrow(
        "The request timed out. Try again."
      )
      await vi.advanceTimersByTimeAsync(30_000)

      await request
    } finally {
      vi.useRealTimers()
    }
  })
})

describe("offline API feedback", () => {
  it("turns a network failure into an actionable offline message", async () => {
    Object.defineProperty(navigator, "onLine", {
      configurable: true,
      value: false,
    })
    try {
      vi.spyOn(globalThis, "fetch").mockRejectedValue(
        new TypeError("Failed to fetch")
      )
      await expect(getCurrentAuth()).rejects.toThrow(
        "You’re offline. Reconnect and try again."
      )
    } finally {
      Object.defineProperty(navigator, "onLine", {
        configurable: true,
        value: true,
      })
    }
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
        target_id: "field-0001",
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
              target_id: "field-0001",
              value: "1180.00",
              reason: "Checked against invoice",
            },
          ],
        }),
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "Idempotency-Key": expect.any(String),
          "X-CSRF-Token": "correction-csrf",
        }),
      })
    )
  })

  it("serializes concurrent CSRF-protected mutations", async () => {
    let releaseFirstMutation
    const firstMutationResponse = new Promise((resolve) => {
      releaseFirstMutation = () =>
        resolve(jsonResponse({ result_id: "result-1", version: 2 }))
    })
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }))
      .mockReturnValueOnce(firstMutationResponse)
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-2" }))
      .mockResolvedValueOnce(
        jsonResponse({ result_id: "result-2", version: 2 })
      )

    const first = correctResult("result-1", 1, [
      { target_id: "field-0001", value: "A" },
    ])
    const second = correctResult("result-2", 1, [
      { target_id: "field-0001", value: "B" },
    ])

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/auth/csrf`)
    expect(fetchMock.mock.calls[1][0]).toBe(
      `${API_BASE_URL}/results/result-1/fields`
    )
    releaseFirstMutation()
    await Promise.all([first, second])
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `${API_BASE_URL}/auth/csrf`,
      `${API_BASE_URL}/results/result-1/fields`,
      `${API_BASE_URL}/auth/csrf`,
      `${API_BASE_URL}/results/result-2/fields`,
    ])
  })
})

describe("document deletion", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it.each([
    [deleteDocumentOriginal, "/documents/document-1/original"],
    [permanentlyDeleteDocument, "/documents/document-1"],
  ])(
    "sends a CSRF-protected DELETE to the intended endpoint",
    async (remove, path) => {
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
            "Idempotency-Key": expect.any(String),
            "X-CSRF-Token": "delete-csrf",
          }),
        })
      )
    }
  )
})

describe("retryDocumentProcessing", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("queues a CSRF-protected retry", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "retry-csrf" }))
      .mockResolvedValueOnce(
        jsonResponse({
          document_id: "document-1",
          run_id: "run-2",
          status: "uploaded",
        })
      )

    await retryDocumentProcessing("document-1")

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `${API_BASE_URL}/documents/document-1/retry`,
      expect.objectContaining({
        credentials: "include",
        method: "POST",
        headers: expect.objectContaining({
          "Idempotency-Key": expect.any(String),
          "X-CSRF-Token": "retry-csrf",
        }),
      })
    )
  })
})
