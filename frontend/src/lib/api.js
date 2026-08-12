const configuredApiBaseUrl = import.meta.env.VITE_API_BASE_URL
const API_REQUEST_TIMEOUT_MS = 30_000

export const API_BASE_URL = (
  configuredApiBaseUrl || "http://localhost:8000/api/v1"
).replace(/\/$/, "")

async function apiRequest(path, options = {}) {
  const controller = new AbortController()
  let timedOut = false
  const abortFromCaller = () => controller.abort(options.signal?.reason)
  if (options.signal?.aborted) abortFromCaller()
  else
    options.signal?.addEventListener("abort", abortFromCaller, { once: true })
  const timeout = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, API_REQUEST_TIMEOUT_MS)
  try {
    return await fetch(`${API_BASE_URL}${path}`, {
      credentials: "include",
      ...options,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...options.headers,
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
    options.signal?.removeEventListener("abort", abortFromCaller)
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

async function getCsrfToken() {
  const response = await apiRequest("/auth/csrf")
  if (!response.ok) {
    throw await responseError(response, "Unable to authorize this request.")
  }
  const payload = await response.json()
  return payload.csrf_token
}

let csrfMutationQueue = Promise.resolve()

function csrfRequest(path, options = {}) {
  const request = async () => {
    const csrfToken = await getCsrfToken()
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
  const response = await apiRequest("/auth/me", { signal })
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

export const ACCEPTED_UPLOAD_TYPES = [
  "application/pdf",
  "image/jpeg",
  "image/png",
]

export const ACCEPTED_UPLOAD_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png"]
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024
export const MAX_UPLOAD_FILES = 10

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

export async function uploadDocument(file, { onStage } = {}) {
  const mimeType = uploadMimeType(file)
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

  onStage?.("uploading")
  const uploadResponse = await fetch(created.upload.url, {
    method: created.upload.method,
    headers: created.upload.headers,
    body: file,
  })
  if (!uploadResponse.ok) {
    throw new Error("The file could not be uploaded to private storage.")
  }

  onStage?.("verifying")
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
  return completeResponse.json()
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
  limit = 25,
  signal,
  status,
} = {}) {
  const query = new URLSearchParams({ limit: String(limit) })
  if (cursor) query.set("cursor", cursor)
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
