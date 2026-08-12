import { useInfiniteQuery } from "@tanstack/react-query"
import { Link, useLocation } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { listDocuments } from "@/lib/api"

const STATUS_LABELS = {
  upload_pending: "Upload pending",
  uploaded: "Queued",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
}

function formatDate(value) {
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}

export function DashboardPage() {
  const location = useLocation()
  const documentsQuery = useInfiniteQuery({
    queryKey: ["documents"],
    queryFn: ({ pageParam, signal }) =>
      listDocuments({ cursor: pageParam, limit: 10, signal }),
    initialPageParam: null,
    getNextPageParam: (page) => page.next_cursor || undefined,
  })
  const documents =
    documentsQuery.data?.pages.flatMap((page) => page.items) || []

  return (
    <section>
      {location.state?.deleted ? (
        <p className="mb-5 rounded-lg bg-muted p-3 text-sm" role="status">
          Document permanently deleted.
        </p>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
            Workspace
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">
            Documents
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Upload, monitor, review, and export accounting documents.
          </p>
        </div>
        <Button nativeButton={false} render={<Link to="/upload" />} size="lg">
          Upload an invoice
        </Button>
      </div>

      <div className="mt-7 overflow-hidden rounded-2xl border bg-card shadow-sm">
        <div className="border-b px-5 py-4">
          <h2 className="font-semibold">Recent documents</h2>
        </div>
        {documentsQuery.isPending ? (
          <p className="p-5 text-sm text-muted-foreground">
            Loading documents…
          </p>
        ) : documentsQuery.error ? (
          <p className="p-5 text-sm text-destructive" role="alert">
            {documentsQuery.error.message}
          </p>
        ) : documents.length ? (
          <ul className="divide-y">
            {documents.map((document) => (
              <li key={document.id}>
                <Link
                  className="flex items-center justify-between gap-4 px-5 py-4 transition-colors hover:bg-muted/50"
                  to={`/documents/${document.id}`}
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {document.original_filename}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {formatDate(document.created_at)}
                    </p>
                  </div>
                  <span className="shrink-0 rounded-full bg-secondary px-3 py-1 text-xs font-medium">
                    {STATUS_LABELS[document.status] || document.status}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <div className="p-8 text-center">
            <p className="text-sm font-medium">No documents yet</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Upload one invoice to start the review workflow.
            </p>
          </div>
        )}
        {documentsQuery.hasNextPage ? (
          <div className="border-t p-4 text-center">
            <Button
              disabled={documentsQuery.isFetchingNextPage}
              onClick={() => documentsQuery.fetchNextPage()}
              variant="outline"
            >
              {documentsQuery.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </div>
        ) : null}
      </div>
    </section>
  )
}
