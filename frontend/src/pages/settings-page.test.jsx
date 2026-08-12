import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it } from "vitest"

import { SettingsPage } from "@/pages/settings-page"

describe("SettingsPage", () => {
  it("shows the authenticated profile, workspace, and retention behavior", () => {
    render(
      <MemoryRouter>
        <SettingsPage
          auth={{
            user: {
              display_name: "Alex Morgan",
              email: "owner@example.test",
            },
            workspaces: [
              { id: "workspace-1", name: "My workspace", role: "owner" },
            ],
          }}
        />
      </MemoryRouter>
    )

    expect(
      screen.getByRole("heading", { name: "Settings" })
    ).toBeInTheDocument()
    expect(screen.getByText("Alex Morgan")).toBeInTheDocument()
    expect(screen.getByText("My workspace")).toBeInTheDocument()
    expect(screen.getByText(/Originals remain private/)).toBeInTheDocument()
  })
})
