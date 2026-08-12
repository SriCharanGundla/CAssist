import { useQuery } from "@tanstack/react-query"
import { Link, useLocation, useParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { getDocument, getRun } from "@/lib/api"

const TERMINAL_RUN_STATUSES = new Set(["succeeded", "failed"])

const STATUS_LABELS = {
  upload_pending: "Upload pending",
  uploaded: "Queued",
  processing: "Processing",
  ready: "Ready for review",
  failed: "Processing failed",
  queued: "Queued",
  preprocessing: "Preparing pages",
  extracting: "Extracting invoice fields",
  validating: "Validating totals and tax fields",
  succeeded: "Extraction complete",
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status.replaceAll("_", " ")
}

export function DocumentPage() {
  const { documentId } = useParams()
  const location = useLocation()
  const documentQuery = useQuery({
    queryKey: ["document", documentId],
    queryFn: ({ signal }) => getDocument(documentId, { signal }),
    refetchInterval: (query) => {
      const document = query.state.data
      if (!document || !["ready", "failed"].includes(document.status)) {
        return 2_000
      }
      return false
    },
  })
  const runId = documentQuery.data?.latest_run?.id
  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: ({ signal }) => getRun(runId, { signal }),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const run = query.state.data
      return run && TERMINAL_RUN_STATUSES.has(run.status) ? false : 2_000
    },
  })

  if (documentQuery.isPending) {
    return <p className="text-sm text-muted-foreground">Loading document…</p>
  }
  if (documentQuery.error) {
    return (
      <p className="text-sm text-destructive" role="alert">
        {documentQuery.error.message}
      </p>
    )
  }

  const document = documentQuery.data
  const run = runQuery.data || document.latest_run
  const displayStatus = run?.status || document.status
  const isComplete = run?.status === "succeeded"
  const failedError = runQuery.data?.error

  return (
    <section className="mx-auto max-w-2xl">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
            Document
          </p>
          <h1 className="mt-2 text-2xl font-semibold break-words">
            {document.original_filename}
          </h1>
        </div>
        <span className="rounded-full bg-secondary px-3 py-1 text-xs font-medium text-secondary-foreground capitalize">
          {statusLabel(displayStatus)}
        </span>
      </div>

      {location.state?.deduplicated ? (
        <p className="mt-5 rounded-lg border bg-card p-4 text-sm">
          This file already existed in your workspace, so CAssist reused the
          existing document and processing history.
        </p>
      ) : null}

      <div className="mt-6 rounded-2xl border bg-card p-6 shadow-sm">
        <h2 className="font-semibold">Processing status</h2>
        <p aria-live="polite" className="mt-2 text-sm text-muted-foreground">
          {isComplete
            ? "Extraction finished. The result still requires human review."
            : run
              ? statusLabel(run.status)
              : "Waiting for the extraction job to appear…"}
        </p>
        {runQuery.data?.progress?.total_pages ? (
          <p className="mt-2 text-xs text-muted-foreground">
            {runQuery.data.progress.completed_pages === null
              ? `${runQuery.data.progress.total_pages} pages detected`
              : `${runQuery.data.progress.completed_pages} of ${runQuery.data.progress.total_pages} pages complete`}
          </p>
        ) : null}
        {failedError ? (
          <p className="mt-4 text-sm text-destructive" role="alert">
            {failedError.message}
          </p>
        ) : null}
        {runQuery.error ? (
          <p className="mt-4 text-sm text-destructive" role="alert">
            {runQuery.error.message}
          </p>
        ) : null}
      </div>

      <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
        <div className="rounded-lg border bg-card p-4">
          <dt className="text-xs text-muted-foreground">File type</dt>
          <dd className="mt-1 font-medium">{document.mime_type}</dd>
        </div>
        <div className="rounded-lg border bg-card p-4">
          <dt className="text-xs text-muted-foreground">Original</dt>
          <dd className="mt-1 font-medium">
            {document.original_available ? "Stored privately" : "Unavailable"}
          </dd>
        </div>
      </dl>

      <div className="mt-6 flex gap-3">
        {isComplete && run.result_id ? (
          <Button disabled title="Review UI is the next milestone">
            Review extraction
          </Button>
        ) : null}
        <Button
          nativeButton={false}
          render={<Link to="/upload" />}
          variant="outline"
        >
          Upload another
        </Button>
      </div>
    </section>
  )
}
