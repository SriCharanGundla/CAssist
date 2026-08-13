import { afterEach, describe, expect, it, vi } from "vitest"

import { onRequest } from "./[[path]].js"

const environment = {
  CASSIST_ORIGIN: "https://cassist-nas.example.ts.net",
  CASSIST_PROXY_SECRET: "p".repeat(32),
}

describe("Pages API proxy", () => {
  afterEach(() => vi.restoreAllMocks())

  it("fails closed when its origin or secret is unavailable", async () => {
    const response = await onRequest({
      env: {},
      request: new Request("https://cassist.pages.dev/api/v1/health"),
    })

    expect(response.status).toBe(503)
    await expect(response.json()).resolves.toMatchObject({
      error: { code: "EDGE_PROXY_UNAVAILABLE" },
    })
  })

  it("streams the request to Funnel with an edge-only secret", async () => {
    const upstream = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("ok", {
        status: 202,
        headers: { "Set-Cookie": "session=opaque; Secure; HttpOnly" },
      })
    )
    const response = await onRequest({
      env: environment,
      request: new Request(
        "https://cassist.pages.dev/api/v1/uploads/document-1/complete?source=test",
        {
          method: "POST",
          headers: {
            Origin: "https://cassist.pages.dev",
            "X-CAssist-Proxy-Secret": "attacker-value",
          },
          body: "{}",
        }
      ),
    })

    expect(response.status).toBe(202)
    expect(response.headers.get("set-cookie")).toContain("session=opaque")
    expect(upstream).toHaveBeenCalledOnce()
    const forwarded = upstream.mock.calls[0][0]
    expect(forwarded.url).toBe(
      "https://cassist-nas.example.ts.net/api/v1/uploads/document-1/complete?source=test"
    )
    expect(forwarded.headers.get("x-cassist-proxy-secret")).toBe(
      environment.CASSIST_PROXY_SECRET
    )
    expect(forwarded.headers.get("x-forwarded-host")).toBe(
      "cassist.pages.dev"
    )
    expect(forwarded.headers.get("origin")).toBe(
      "https://cassist.pages.dev"
    )
    await expect(forwarded.text()).resolves.toBe("{}")
  })

  it("supplies the trusted Pages origin for same-origin CSRF bootstrap", async () => {
    const upstream = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("ok"))

    await onRequest({
      env: environment,
      request: new Request("https://cassist.pages.dev/api/v1/auth/csrf"),
    })

    expect(upstream.mock.calls[0][0].headers.get("origin")).toBe(
      "https://cassist.pages.dev"
    )
  })

  it("does not trust a client-supplied origin", async () => {
    const upstream = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("ok"))

    await onRequest({
      env: environment,
      request: new Request("https://cassist.pages.dev/api/v1/auth/csrf", {
        headers: { Origin: "https://attacker.example" },
      }),
    })

    expect(upstream.mock.calls[0][0].headers.get("origin")).toBe(
      "https://cassist.pages.dev"
    )
  })

  it("does not forward to an insecure origin", async () => {
    const upstream = vi.spyOn(globalThis, "fetch")
    const response = await onRequest({
      env: { ...environment, CASSIST_ORIGIN: "http://localhost:8000" },
      request: new Request("https://cassist.pages.dev/api/v1/health"),
    })

    expect(response.status).toBe(503)
    expect(upstream).not.toHaveBeenCalled()
  })

  it("sanitizes non-JSON origin failures", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<title>Origin DNS error | private-origin</title>", {
        status: 530,
        headers: { "Content-Type": "text/html" },
      })
    )

    const response = await onRequest({
      env: environment,
      request: new Request("https://cassist.pages.dev/api/v1/health"),
    })

    expect(response.status).toBe(503)
    expect(response.headers.get("cache-control")).toBe("no-store")
    await expect(response.json()).resolves.toEqual({
      error: { code: "EDGE_PROXY_UNAVAILABLE", message: "API unavailable" },
    })
  })
})
