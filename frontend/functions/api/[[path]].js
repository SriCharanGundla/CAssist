const PROXY_HEADER = "X-CAssist-Proxy-Secret"

function configuredOrigin(value) {
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

function unavailable() {
  return Response.json(
    { error: { code: "EDGE_PROXY_UNAVAILABLE", message: "API unavailable" } },
    { status: 503, headers: { "Cache-Control": "no-store" } }
  )
}

export async function onRequest(context) {
  const origin = configuredOrigin(context.env.CASSIST_ORIGIN)
  const proxySecret = context.env.CASSIST_PROXY_SECRET
  if (!origin || typeof proxySecret !== "string" || proxySecret.length < 32) {
    return unavailable()
  }

  const incomingUrl = new URL(context.request.url)
  const targetUrl = new URL(`${incomingUrl.pathname}${incomingUrl.search}`, origin)
  const headers = new Headers(context.request.headers)
  headers.set(PROXY_HEADER, proxySecret)
  headers.set("X-Forwarded-Host", incomingUrl.host)
  headers.set("X-Forwarded-Proto", "https")

  const upstreamRequest = new Request(targetUrl, {
    method: context.request.method,
    headers,
    body:
      context.request.method === "GET" || context.request.method === "HEAD"
        ? null
        : context.request.body,
    duplex: "half",
    redirect: "manual",
  })

  try {
    const response = await fetch(upstreamRequest)
    const contentType = response.headers.get("content-type") || ""
    if (response.status >= 500 && !contentType.includes("application/json")) {
      response.body?.cancel()
      return unavailable()
    }
    return new Response(response.body, response)
  } catch {
    return unavailable()
  }
}
