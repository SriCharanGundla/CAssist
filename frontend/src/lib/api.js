const configuredApiBaseUrl = import.meta.env.VITE_API_BASE_URL

export const API_BASE_URL = (
  configuredApiBaseUrl || "http://localhost:8000/api/v1"
).replace(/\/$/, "")

async function apiRequest(path, options = {}) {
  return fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    ...options,
    headers: {
      Accept: "application/json",
      ...options.headers,
    },
  })
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
  const csrfResponse = await apiRequest("/auth/csrf")
  if (!csrfResponse.ok) {
    throw new Error("Unable to create a logout request.")
  }
  const { csrf_token: csrfToken } = await csrfResponse.json()
  const response = await apiRequest("/auth/logout", {
    method: "POST",
    headers: {
      "X-CSRF-Token": csrfToken,
    },
  })
  if (!response.ok) {
    throw new Error("Unable to sign out.")
  }
  return response.json()
}
