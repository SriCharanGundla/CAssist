import { render, screen, waitFor } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { ConnectionStatus } from "@/components/connection-status"

function setOnline(value) {
  Object.defineProperty(navigator, "onLine", {
    configurable: true,
    value,
  })
}

describe("ConnectionStatus", () => {
  it("shows offline state and clears it after reconnection", async () => {
    setOnline(true)
    render(<ConnectionStatus />)
    expect(screen.queryByRole("status")).not.toBeInTheDocument()

    setOnline(false)
    window.dispatchEvent(new Event("offline"))
    expect(await screen.findByRole("status")).toHaveTextContent(
      "You’re offline. Reconnect to continue."
    )

    setOnline(true)
    window.dispatchEvent(new Event("online"))
    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument()
    )
  })
})
