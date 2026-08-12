import { describe, expect, it } from "vitest"

import { adaptivePollingInterval } from "@/lib/polling"

const TERMINAL = new Set(["succeeded", "failed"])

function query(status, dataUpdateCount) {
  return { state: { data: status ? { status } : undefined, dataUpdateCount } }
}

describe("adaptivePollingInterval", () => {
  it("backs off after repeated updates and stops at terminal state", () => {
    expect(adaptivePollingInterval(query(null, 0), TERMINAL)).toBe(2_000)
    expect(adaptivePollingInterval(query("processing", 2), TERMINAL)).toBe(
      2_000
    )
    expect(adaptivePollingInterval(query("processing", 3), TERMINAL)).toBe(
      5_000
    )
    expect(adaptivePollingInterval(query("processing", 6), TERMINAL)).toBe(
      10_000
    )
    expect(adaptivePollingInterval(query("succeeded", 7), TERMINAL)).toBe(false)
  })
})
