const configuredApiBaseUrl = import.meta.env.VITE_API_BASE_URL
const API_REQUEST_TIMEOUT_MS = 30_000
const AUTH_REQUEST_TIMEOUT_MS = 30_000

export const API_BASE_URL = (
  configuredApiBaseUrl ||
  (import.meta.env.DEV ? "http://localhost:8000/api/v1" : "/api/v1")
).replace(/\/$/, "")

async function apiRequest(path, options = {}) {
  const fetchOptions = { ...options }
  const callerSignal = fetchOptions.signal
  const timeoutMs = fetchOptions.timeoutMs ?? API_REQUEST_TIMEOUT_MS
  delete fetchOptions.idempotencyKey
  delete fetchOptions.signal
  delete fetchOptions.timeoutMs
  const controller = new AbortController()
  let timedOut = false
  const abortFromCaller = () => controller.abort(callerSignal?.reason)
  if (callerSignal?.aborted) abortFromCaller()
  else callerSignal?.addEventListener("abort", abortFromCaller, { once: true })
  const timeout = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  try {
    return await fetch(`${API_BASE_URL}${path}`, {
      credentials: "include",
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...fetchOptions.headers,
      },
    })
  } catch (error) {
    if (timedOut)
      throw new Error("The request timed out. Try again.", { cause: error })
    if (!navigator.onLine)
      throw new Error("You’re offline. Reconnect and try again.", {
        cause: error,
      })
    throw error
  } finally {
    window.clearTimeout(timeout)
    callerSignal?.removeEventListener("abort", abortFromCaller)
  }
}

async function responseError(response, fallbackMessage) {
  let message = fallbackMessage
  try {
    const payload = await response.json()
    if (typeof payload.error?.message === "string") {
      message = payload.error.message
    } else if (typeof payload.detail === "string") {
      message = payload.detail
    }
  } catch {
    // The fallback is intentionally safe for non-JSON upstream responses.
  }
  const error = new Error(message)
  error.status = response.status
  return error
}

async function getCsrfToken({ signal } = {}) {
  const response = await apiRequest("/auth/csrf", { signal })
  if (!response.ok) {
    throw await responseError(response, "Unable to authorize this request.")
  }
  const payload = await response.json()
  return payload.csrf_token
}

let csrfMutationQueue = Promise.resolve()

function csrfRequest(path, options = {}) {
  const request = async () => {
    const csrfToken = await getCsrfToken({ signal: options.signal })
    const idempotencyKey = options.idempotencyKey || crypto.randomUUID()
    return apiRequest(path, {
      ...options,
      headers: {
        ...options.headers,
        "Idempotency-Key": idempotencyKey,
        "X-CSRF-Token": csrfToken,
      },
    })
  }
  const response = csrfMutationQueue.then(request, request)
  csrfMutationQueue = response.then(
    () => undefined,
    () => undefined
  )
  return response
}

export function loginUrl(returnTo = "/") {
  const query = new URLSearchParams({ return_to: returnTo })
  return `${API_BASE_URL}/auth/login?${query}`
}

export async function getCurrentAuth({ signal } = {}) {
  const response = await apiRequest("/auth/me", {
    signal,
    timeoutMs: AUTH_REQUEST_TIMEOUT_MS,
  })
  if (response.status === 401) {
    return null
  }
  if (!response.ok) {
    throw new Error(
      response.status === 503
        ? "Authentication is not configured yet."
        : "Unable to load the current session."
    )
  }
  return response.json()
}

export async function logout() {
  const response = await csrfRequest("/auth/logout", {
    method: "POST",
  })
  if (!response.ok) {
    throw await responseError(response, "Unable to sign out.")
  }
  return response.json()
}

export async function listAuthSessions({
  page = 1,
  pageSize = 5,
  signal,
} = {}) {
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  const response = await apiRequest(`/auth/sessions?${query}`, {
    signal,
    timeoutMs: AUTH_REQUEST_TIMEOUT_MS,
  })
  if (!response.ok) {
    throw await responseError(response, "Unable to load active sessions.")
  }
  return response.json()
}

export async function revokeAuthSession(sessionId) {
  const response = await csrfRequest(`/auth/sessions/${sessionId}`, {
    method: "DELETE",
  })
  if (!response.ok) {
    throw await responseError(response, "Unable to sign out that device.")
  }
}

const UPLOAD_MIME_TYPE_BY_EXTENSION = {
  ".pdf": "application/pdf",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
}

export function uploadMimeType(file) {
  const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase()
  return UPLOAD_MIME_TYPE_BY_EXTENSION[extension] || file.type
}

function uploadToPrivateStorage(file, upload, { onProgress, signal } = {}) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    const abort = () => request.abort()
    request.open(upload.method, upload.url)
    for (const [name, value] of Object.entries(upload.headers)) {
      // Browsers set Content-Length from the immutable File body. R2 still
      // verifies that automatically-generated value against the signed header.
      if (name.toLowerCase() !== "content-length") {
        request.setRequestHeader(name, value)
      }
    }
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) {
        onProgress?.(Math.round((event.loaded / event.total) * 100))
      }
    })
    request.addEventListener("load", () => {
      signal?.removeEventListener("abort", abort)
      if (request.status >= 200 && request.status < 300) resolve()
      else
        reject(new Error("The file could not be uploaded to private storage."))
    })
    request.addEventListener("error", () => {
      signal?.removeEventListener("abort", abort)
      reject(new Error("The file could not be uploaded to private storage."))
    })
    request.addEventListener("abort", () => {
      signal?.removeEventListener("abort", abort)
      reject(new DOMException("Upload cancelled", "AbortError"))
    })
    if (signal?.aborted) {
      reject(new DOMException("Upload cancelled", "AbortError"))
      return
    }
    signal?.addEventListener("abort", abort, { once: true })
    request.send(file)
  })
}

export async function uploadDocument(
  file,
  { onProgress, onStage, signal } = {}
) {
  const mimeType = uploadMimeType(file)
  if (signal?.aborted) {
    throw new DOMException("Upload cancelled", "AbortError")
  }
  onStage?.("creating")
  const createResponse = await csrfRequest("/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      filename: file.name,
      mime_type: mimeType,
      byte_size: file.size,
    }),
  })
  if (!createResponse.ok) {
    throw await responseError(createResponse, "Unable to create the upload.")
  }
  const created = await createResponse.json()

  try {
    if (signal?.aborted) {
      throw new DOMException("Upload cancelled", "AbortError")
    }
    onStage?.("uploading")
    onProgress?.(0)
    await uploadToPrivateStorage(file, created.upload, { onProgress, signal })
    onProgress?.(100)

    if (signal?.aborted) {
      throw new DOMException("Upload cancelled", "AbortError")
    }
    onStage?.("verifying")
    // Do not abort an ambiguous completion request. If the user cancels while it
    // is in flight, wait for it and then remove the resulting queued document.
    const completeResponse = await csrfRequest(
      `/uploads/${created.document_id}/complete`,
      { method: "POST" }
    )
    if (!completeResponse.ok) {
      throw await responseError(
        completeResponse,
        "The uploaded file could not be verified."
      )
    }
    const result = await completeResponse.json()
    if (signal?.aborted) {
      throw new DOMException("Upload cancelled", "AbortError")
    }
    return result
  } catch (error) {
    if (error.name === "AbortError") {
      const cancelResponse = await csrfRequest(
        `/uploads/${created.document_id}`,
        { method: "DELETE" }
      )
      if (!cancelResponse.ok) {
        throw await responseError(
          cancelResponse,
          "Upload cancellation could not be confirmed."
        )
      }
    }
    throw error
  }
}

export async function getStorageQuota({ signal } = {}) {
  const response = await apiRequest("/uploads/quota", { signal })
  if (!response.ok) {
    throw await responseError(response, "Unable to load shared storage usage.")
  }
  return response.json()
}

export async function getUploadCapabilities({ signal } = {}) {
  const response = await apiRequest("/uploads/capabilities", { signal })
  if (!response.ok) {
    throw await responseError(response, "Unable to load upload limits.")
  }
  return response.json()
}

export async function getDocument(documentId, { signal } = {}) {
  const response = await apiRequest(`/documents/${documentId}`, { signal })
  if (!response.ok) {
    throw await responseError(response, "Unable to load the document.")
  }
  return response.json()
}

export async function listDocuments({
  cursor,
  documentType,
  limit = 25,
  search,
  signal,
  status,
} = {}) {
  const query = new URLSearchParams({ limit: String(limit) })
  if (cursor) query.set("cursor", cursor)
  if (documentType) query.set("document_type", documentType)
  if (search) query.set("search", search)
  if (status) query.set("status", status)
  const response = await apiRequest(`/documents?${query}`, { signal })
  if (!response.ok) {
    throw await responseError(response, "Unable to load recent documents.")
  }
  return response.json()
}

export async function getRun(runId, { signal } = {}) {
  const response = await apiRequest(`/runs/${runId}`, { signal })
  if (!response.ok) {
    throw await responseError(response, "Unable to load processing status.")
  }
  return response.json()
}

export async function getResult(resultId, { signal } = {}) {
  const response = await apiRequest(`/results/${resultId}`, { signal })
  if (!response.ok) {
    throw await responseError(response, "Unable to load the extraction result.")
  }
  return response.json()
}

export async function correctResult(resultId, expectedVersion, changes) {
  const response = await csrfRequest(`/results/${resultId}/fields`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_version: expectedVersion, changes }),
  })
  if (!response.ok) {
    throw await responseError(response, "Unable to save the correction.")
  }
  return response.json()
}

export async function updateResultReview(resultId, expectedVersion, status) {
  const response = await csrfRequest(`/results/${resultId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_version: expectedVersion, status }),
  })
  if (!response.ok) {
    throw await responseError(response, "Unable to update the review status.")
  }
  return response.json()
}

export async function updateTallySelection(
  resultId,
  expectedVersion,
  excludedTargetIds
) {
  const response = await csrfRequest(`/results/${resultId}/selection`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_version: expectedVersion,
      excluded_target_ids: excludedTargetIds,
    }),
  })
  if (!response.ok) {
    throw await responseError(response, "Unable to save the Tally selection.")
  }
  return response.json()
}

export async function createOriginalViewUrl(documentId) {
  const response = await csrfRequest(`/documents/${documentId}/view-url`, {
    method: "POST",
  })
  if (!response.ok) {
    throw await responseError(response, "Unable to open the original document.")
  }
  return response.json()
}

export async function retryDocumentProcessing(documentId) {
  const response = await csrfRequest(`/documents/${documentId}/retry`, {
    method: "POST",
  })
  if (!response.ok) {
    throw await responseError(response, "Unable to retry document extraction.")
  }
  return response.json()
}

export async function confirmDocumentProcessing(documentId) {
  const response = await csrfRequest(
    `/documents/${documentId}/confirm-processing`,
    { method: "POST" }
  )
  if (!response.ok) {
    throw await responseError(
      response,
      "Unable to confirm document processing."
    )
  }
  return response.json()
}

export async function createProcessingRun(documentId, options = {}) {
  const response = await csrfRequest(`/documents/${documentId}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  })
  if (!response.ok) {
    throw await responseError(response, "Unable to queue document extraction.")
  }
  return response.json()
}

export async function cancelProcessingRun(runId) {
  const response = await csrfRequest(`/runs/${runId}/cancel`, {
    method: "POST",
  })
  if (!response.ok) {
    throw await responseError(response, "Unable to cancel document extraction.")
  }
  return response.json()
}

export async function compareDocument(documentId) {
  const response = await csrfRequest(`/documents/${documentId}/comparisons`, {
    method: "POST",
  })
  if (!response.ok) {
    throw await responseError(response, "Unable to compare extraction models.")
  }
  return response.json()
}

export async function deleteDocumentOriginal(documentId) {
  const response = await csrfRequest(`/documents/${documentId}/original`, {
    method: "DELETE",
  })
  if (!response.ok) {
    throw await responseError(
      response,
      "Unable to delete the original document."
    )
  }
}

export async function permanentlyDeleteDocument(documentId) {
  const response = await csrfRequest(`/documents/${documentId}`, {
    method: "DELETE",
  })
  if (!response.ok) {
    throw await responseError(
      response,
      "Unable to permanently delete the document."
    )
  }
}

export async function downloadTallyExport(resultId, expectedVersion) {
  const response = await csrfRequest(`/results/${resultId}/exports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_version: expectedVersion,
      format: "tally_json",
      options: { include_quality_issues: false },
    }),
  })
  if (!response.ok) {
    throw await responseError(
      response,
      "Unable to create the Tally JSON export."
    )
  }
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  try {
    const link = document.createElement("a")
    link.href = objectUrl
    link.download = `cassist-tally-${resultId}.json`
    link.click()
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}
