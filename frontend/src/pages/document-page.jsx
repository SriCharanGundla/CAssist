import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useLocation, useNavigate, useParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import {
  createOriginalViewUrl,
  deleteDocumentOriginal,
  getDocument,
  getRun,
  permanentlyDeleteDocument,
} from "@/lib/api"

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
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = React.useState(null)
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
  const viewMutation = useMutation({
    mutationFn: () => createOriginalViewUrl(documentId),
    onSuccess: ({ url }) => window.open(url, "_blank", "noopener,noreferrer"),
  })
  const originalDeleteMutation = useMutation({
    mutationFn: () => deleteDocumentOriginal(documentId),
    onSuccess: async () => {
      setConfirming(null)
      await queryClient.invalidateQueries({
        queryKey: ["document", documentId],
      })
    },
  })
  const permanentDeleteMutation = useMutation({
    mutationFn: () => permanentlyDeleteDocument(documentId),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ["document", documentId] })
      navigate("/", { replace: true, state: { deleted: true } })
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
  const actionError =
    viewMutation.error ||
    originalDeleteMutation.error ||
    permanentDeleteMutation.error

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
          <Button
            nativeButton={false}
            render={<Link to={`/results/${run.result_id}/review`} />}
          >
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

      <section className="mt-8 rounded-2xl border bg-card p-5 shadow-sm">
        <h2 className="font-semibold">Original and retention</h2>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          Viewing creates a five-minute private link. Deleting only the original
          keeps extraction and audit history; permanent deletion removes the
          full record.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button
            disabled={!document.original_available || viewMutation.isPending}
            onClick={() => viewMutation.mutate()}
            variant="outline"
          >
            {viewMutation.isPending ? "Opening…" : "Open original"}
          </Button>
          {document.original_available ? (
            <Button
              onClick={() => setConfirming("original")}
              variant="destructive"
            >
              Delete original only
            </Button>
          ) : null}
          <Button
            onClick={() => setConfirming("permanent")}
            variant="destructive"
          >
            Permanently delete record
          </Button>
        </div>
        {confirming ? (
          <div className="mt-4 rounded-lg border border-destructive/30 bg-destructive/10 p-4">
            <p className="text-sm font-medium">
              {confirming === "original"
                ? "Delete the private original file? Extraction and history will remain."
                : "Permanently delete this document, its extraction, corrections, and export history?"}
            </p>
            <div className="mt-3 flex gap-2">
              <Button
                disabled={
                  originalDeleteMutation.isPending ||
                  permanentDeleteMutation.isPending
                }
                onClick={() =>
                  confirming === "original"
                    ? originalDeleteMutation.mutate()
                    : permanentDeleteMutation.mutate()
                }
                variant="destructive"
              >
                {originalDeleteMutation.isPending ||
                permanentDeleteMutation.isPending
                  ? "Deleting…"
                  : "Confirm deletion"}
              </Button>
              <Button onClick={() => setConfirming(null)} variant="ghost">
                Cancel
              </Button>
            </div>
          </div>
        ) : null}
        {actionError ? (
          <p className="mt-4 text-sm text-destructive" role="alert">
            {actionError.message}
          </p>
        ) : null}
      </section>
    </section>
  )
}
