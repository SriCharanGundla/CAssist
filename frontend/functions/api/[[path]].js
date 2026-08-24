// @ts-check

const PROXY_HEADER = "X-CAssist-Proxy-Secret"

/** @typedef {{ CASSIST_ORIGIN?: string, CASSIST_PROXY_SECRET?: string }} ProxyEnvironment */

/** @param {string | undefined} value */
function configuredOrigin(value) {
  if (!value) return null
  try {
    const origin = new URL(value)
    if (
      origin.protocol !== "https:" ||
      origin.username ||
      origin.password ||
      origin.pathname !== "/" ||
      origin.search ||
      origin.hash
    ) {
      return null
    }
    return origin
  } catch {
    return null
  }
}

/** @param {string} [requestId] */
function unavailable(requestId = crypto.randomUUID()) {
  return Response.json(
    { error: { code: "EDGE_PROXY_UNAVAILABLE", message: "API unavailable" } },
    {
      status: 503,
      headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Request-ID": requestId,
      },
    }
  )
}

/** @type {PagesFunction<ProxyEnvironment>} */
export async function onRequest(context) {
  const requestId = context.request.headers.get("cf-ray") || crypto.randomUUID()
  const origin = configuredOrigin(context.env.CASSIST_ORIGIN)
  const proxySecret = context.env.CASSIST_PROXY_SECRET
  if (!origin || typeof proxySecret !== "string" || proxySecret.length < 32) {
    console.error(
      JSON.stringify({
        message: "Pages API proxy configuration is unavailable",
        request_id: requestId,
      })
    )
    return unavailable(requestId)
  }

  const incomingUrl = new URL(context.request.url)
  const targetUrl = new URL(
    `${incomingUrl.pathname}${incomingUrl.search}`,
    origin
  )
  const headers = new Headers(context.request.headers)
  headers.set(PROXY_HEADER, proxySecret)
  // Same-origin GET requests do not reliably include Origin. The API requires
  // it when issuing a CSRF token, so derive the trusted frontend origin from
  // the Pages request URL instead of accepting a client-supplied value.
  headers.set("Origin", incomingUrl.origin)
  headers.set("X-Forwarded-Host", incomingUrl.host)
  headers.set("X-Forwarded-Proto", "https")

  const upstreamRequest = new Request(targetUrl, {
    method: context.request.method,
    headers,
    body:
      context.request.method === "GET" || context.request.method === "HEAD"
        ? null
        : context.request.body,
    // @ts-expect-error Node's Fetch requires duplex for streamed request bodies;
    // workerd accepts the same initializer although its generated type omits it.
    duplex: "half",
    redirect: "manual",
  })

  try {
    const response = await fetch(upstreamRequest)
    const contentType = response.headers.get("content-type") || ""
    if (response.status >= 500 && !contentType.includes("application/json")) {
      await response.body?.cancel()
      console.error(
        JSON.stringify({
          message: "Pages API proxy sanitized a non-JSON origin failure",
          request_id: requestId,
          upstream_status: response.status,
        })
      )
      return unavailable(requestId)
    }
    const headers = new Headers(response.headers)
    headers.set("X-Content-Type-Options", "nosniff")
    headers.set("Referrer-Policy", "no-referrer")
    headers.set("X-Frame-Options", "DENY")
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    })
  } catch (error) {
    console.error(
      JSON.stringify({
        message: "Pages API proxy request failed",
        request_id: requestId,
        error_type: error instanceof Error ? error.name : "UnknownError",
      })
    )
    return unavailable(requestId)
  }
}
