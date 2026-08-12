import { RiArrowLeftSLine } from "@remixicon/react"
import { Link } from "react-router-dom"

import { Button } from "@/components/ui/button"

function SettingCard({ children, title }) {
  return (
    <section className="rounded-2xl border bg-card p-5 shadow-sm">
      <h2 className="font-semibold">{title}</h2>
      <div className="mt-3 text-sm text-muted-foreground">{children}</div>
    </section>
  )
}

export function SettingsPage({ auth }) {
  return (
    <section className="mx-auto max-w-3xl">
      <Button
        className="mb-5 -ml-2"
        nativeButton={false}
        render={<Link to="/" />}
        variant="ghost"
      >
        <RiArrowLeftSLine /> Back
      </Button>
      <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
        Account
      </p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">Settings</h1>

      <div className="mt-7 grid gap-5 md:grid-cols-2">
        <SettingCard title="Profile">
          <p className="font-medium text-foreground">
            {auth.user.display_name || auth.user.email}
          </p>
          <p className="mt-1">{auth.user.email}</p>
          <p className="mt-3 text-xs">
            Sign-in is managed through your authorized Google account.
          </p>
        </SettingCard>
        <SettingCard title="Workspace">
          {auth.workspaces.map((workspace) => (
            <div
              className="flex items-center justify-between gap-3"
              key={workspace.id}
            >
              <span className="font-medium text-foreground">
                {workspace.name}
              </span>
              <span className="rounded-full bg-secondary px-2 py-0.5 text-xs text-secondary-foreground capitalize">
                {workspace.role}
              </span>
            </div>
          ))}
        </SettingCard>
        <SettingCard title="Original files">
          Originals remain private and are retained until you explicitly delete
          them. Temporary processing files are removed after each attempt.
        </SettingCard>
        <SettingCard title="Extraction data">
          Deleting only a file keeps its reviewed extraction, corrections, audit
          history, and exports. Deleting file and data permanently removes the
          document record.
        </SettingCard>
      </div>
    </section>
  )
}
