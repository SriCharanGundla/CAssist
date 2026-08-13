import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  RiArrowLeftSLine,
  RiComputerLine,
  RiLogoutBoxRLine,
} from "@remixicon/react"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination"
import { listAuthSessions, revokeAuthSession } from "@/lib/api"

function SettingCard({ children, title }) {
  return (
    <section className="rounded-2xl border bg-card p-5 shadow-sm">
      <h2 className="font-semibold">{title}</h2>
      <div className="mt-3 text-sm text-muted-foreground">{children}</div>
    </section>
  )
}

export function SettingsPage({ auth }) {
  const [page, setPage] = React.useState(1)
  const queryClient = useQueryClient()
  const sessionsQuery = useQuery({
    queryKey: ["auth-sessions", page],
    queryFn: ({ signal }) => listAuthSessions({ page, pageSize: 5, signal }),
  })
  const revokeMutation = useMutation({
    mutationFn: revokeAuthSession,
    onSuccess: async () => {
      if (page > 1 && sessionsQuery.data?.items.length === 1) {
        setPage((value) => value - 1)
      }
      await queryClient.invalidateQueries({ queryKey: ["auth-sessions"] })
      toast.success("Device signed out.")
    },
    onError: (error) => toast.error(error.message),
  })

  const formatDateTime = (value) =>
    new Intl.DateTimeFormat("en-IN", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value))

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
        <section className="overflow-hidden rounded-2xl border bg-card shadow-sm md:col-span-2">
          <div className="flex items-start justify-between gap-4 border-b p-5">
            <div>
              <h2 className="font-semibold">Active sessions</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Up to 10 devices can stay signed in.
              </p>
            </div>
            {sessionsQuery.data ? (
              <Badge variant="secondary">
                {sessionsQuery.data.total} active
              </Badge>
            ) : null}
          </div>

          {sessionsQuery.isPending ? (
            <p className="p-5 text-sm text-muted-foreground">
              Loading sessions…
            </p>
          ) : sessionsQuery.isError ? (
            <div className="flex items-center justify-between gap-4 p-5">
              <p className="text-sm text-destructive">
                {sessionsQuery.error.message}
              </p>
              <Button
                onClick={() => sessionsQuery.refetch()}
                size="sm"
                variant="outline"
              >
                Retry
              </Button>
            </div>
          ) : (
            <div className="divide-y">
              {sessionsQuery.data.items.map((item) => (
                <div className="flex items-center gap-3 p-5" key={item.id}>
                  <span className="grid size-10 shrink-0 place-items-center rounded-full bg-muted">
                    <RiComputerLine className="size-5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium">{item.device_label}</p>
                      {item.is_current ? <Badge>Current</Badge> : null}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Signed in {formatDateTime(item.created_at)} · Last active{" "}
                      {formatDateTime(item.last_seen_at)}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      Expires if inactive {formatDateTime(item.expires_at)}
                    </p>
                  </div>
                  {!item.is_current ? (
                    <Button
                      aria-label={`Sign out ${item.device_label}`}
                      disabled={revokeMutation.isPending}
                      onClick={() => revokeMutation.mutate(item.id)}
                      size="sm"
                      variant="destructive"
                    >
                      <RiLogoutBoxRLine /> Sign out
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          )}

          {sessionsQuery.data?.total_pages > 1 ? (
            <Pagination className="border-t p-4">
              <PaginationContent>
                {page > 1 ? (
                  <PaginationItem>
                    <PaginationPrevious
                      href="#"
                      onClick={(event) => {
                        event.preventDefault()
                        setPage((value) => value - 1)
                      }}
                    />
                  </PaginationItem>
                ) : null}
                <PaginationItem>
                  <PaginationLink
                    href="#"
                    isActive
                    onClick={(event) => event.preventDefault()}
                  >
                    {page}
                  </PaginationLink>
                </PaginationItem>
                {page < sessionsQuery.data.total_pages ? (
                  <PaginationItem>
                    <PaginationNext
                      href="#"
                      onClick={(event) => {
                        event.preventDefault()
                        setPage((value) => value + 1)
                      }}
                    />
                  </PaginationItem>
                ) : null}
              </PaginationContent>
            </Pagination>
          ) : null}
        </section>
      </div>
    </section>
  )
}
