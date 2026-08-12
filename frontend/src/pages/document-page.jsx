import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { RiArrowLeftSLine, RiExternalLinkLine } from "@remixicon/react"
import { Link, useLocation, useNavigate, useParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  createOriginalViewUrl,
  deleteDocumentOriginal,
  getDocument,
  getRun,
  permanentlyDeleteDocument,
  retryDocumentProcessing,
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
  preparing: "Preparing pages",
  classifying: "Classifying document",
  extracting: "Extracting document values",
  quality_check: "Checking quality",
  validating: "Checking extracted structure",
  saving: "Saving extraction",
  complete: "Extraction complete",
  succeeded: "Extraction complete",
}

const PROGRESS_LABELS = {
  queued: "Queued...",
  preprocessing: "Preparing...",
  preparing: "Preparing...",
  classifying: "Classifying...",
  extracting: "Extracting...",
  quality_check: "Checking quality...",
  validating: "Saving...",
  saving: "Saving...",
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status.replaceAll("_", " ")
}

function progressLabel(stage) {
  return PROGRESS_LABELS[stage] || statusLabel(stage)
}

function fileTypeLabel(mimeType) {
  return (
    {
      "application/pdf": "PDF",
      "image/jpeg": "JPEG",
      "image/png": "PNG",
    }[mimeType] || mimeType
  )
}

function statusBadgeClass(status) {
  if (["ready", "succeeded"].includes(status)) {
    return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
  }
  if (status === "failed") {
    return "bg-destructive/15 text-destructive"
  }
  return "bg-secondary text-secondary-foreground"
}

export function DocumentPage() {
  const { documentId } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false)
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
  const retryMutation = useMutation({
    mutationFn: () => retryDocumentProcessing(documentId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["document", documentId],
      })
    },
  })
  const originalDeleteMutation = useMutation({
    mutationFn: () => deleteDocumentOriginal(documentId),
    onSuccess: async () => {
      setDeleteDialogOpen(false)
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
  const isRunning = Boolean(
    run?.status && !TERMINAL_RUN_STATUSES.has(run.status)
  )
  const progressStage = runQuery.data?.progress?.stage || run?.status
  const progressHeading = run
    ? progressLabel(progressStage)
    : "Waiting for processing..."
  const failedError = runQuery.data?.error
  const actionError =
    viewMutation.error ||
    retryMutation.error ||
    originalDeleteMutation.error ||
    permanentDeleteMutation.error

  return (
    <section className="mx-auto max-w-2xl">
      <Button
        className="mb-5 -ml-2"
        nativeButton={false}
        render={<Link to="/" />}
        variant="ghost"
      >
        <RiArrowLeftSLine /> Back
      </Button>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
            Document
          </p>
          <h1 className="mt-2 text-2xl font-semibold break-words">
            {document.original_available ? (
              <button
                aria-label={`Open ${document.original_filename} in a new tab`}
                className="group inline-flex max-w-full items-center gap-1.5 text-left"
                disabled={viewMutation.isPending}
                onClick={() => viewMutation.mutate()}
                type="button"
              >
                <span className="break-all group-hover:underline group-focus-visible:underline">
                  {document.original_filename}
                </span>
                <RiExternalLinkLine className="size-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
              </button>
            ) : (
              document.original_filename
            )}
          </h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="rounded-full border bg-card px-3 py-1 text-xs font-medium">
            {fileTypeLabel(document.mime_type)}
          </span>
          <span
            className={`rounded-full px-3 py-1 text-xs font-medium ${statusBadgeClass(displayStatus)}`}
          >
            {statusLabel(displayStatus)}
          </span>
        </div>
      </div>

      {location.state?.deduplicated ? (
        <p className="mt-5 rounded-lg border bg-card p-4 text-sm">
          This file already existed in your workspace, so CAssist reused the
          existing document and processing history.
        </p>
      ) : null}

      <div className="mt-6 rounded-2xl border bg-card p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {isRunning ? (
              <span
                aria-label="Processing"
                className="size-4 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-primary"
                role="status"
              />
            ) : null}
            <h2 aria-live="polite" className="font-semibold">
              {progressHeading}
            </h2>
          </div>
          <div className="flex flex-wrap gap-2">
            {isComplete && run.result_id ? (
              <Button
                nativeButton={false}
                render={<Link to={`/results/${run.result_id}/review`} />}
              >
                Review
              </Button>
            ) : null}
            {run?.status === "failed" && document.original_available ? (
              <Button
                disabled={retryMutation.isPending}
                onClick={() => retryMutation.mutate()}
              >
                {retryMutation.isPending ? "Retrying…" : "Retry extraction"}
              </Button>
            ) : null}
            <Button
              disabled={isRunning}
              onClick={() => setDeleteDialogOpen(true)}
              variant="destructive"
            >
              Delete
            </Button>
          </div>
        </div>
        {!isComplete && runQuery.data?.progress?.total_pages ? (
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
        {actionError ? (
          <p className="mt-4 text-sm text-destructive" role="alert">
            {actionError.message}
          </p>
        ) : null}
      </div>

      <Dialog onOpenChange={setDeleteDialogOpen} open={deleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete document?</DialogTitle>
            <DialogDescription>
              Choose whether to keep the extracted data for review and export.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="sm:justify-start">
            {document.original_available ? (
              <Button
                disabled={
                  originalDeleteMutation.isPending ||
                  permanentDeleteMutation.isPending
                }
                onClick={() => originalDeleteMutation.mutate()}
                variant="outline"
              >
                {originalDeleteMutation.isPending
                  ? "Deleting…"
                  : "Delete File, Keep Data"}
              </Button>
            ) : null}
            <Button
              disabled={
                originalDeleteMutation.isPending ||
                permanentDeleteMutation.isPending
              }
              onClick={() => permanentDeleteMutation.mutate()}
              variant="destructive"
            >
              {permanentDeleteMutation.isPending
                ? "Deleting…"
                : document.original_available
                  ? "Delete File and Data"
                  : "Delete Data"}
            </Button>
            <DialogClose render={<Button variant="ghost" />}>
              Cancel
            </DialogClose>
          </DialogFooter>
          {originalDeleteMutation.error || permanentDeleteMutation.error ? (
            <p className="text-sm text-destructive" role="alert">
              {
                (originalDeleteMutation.error || permanentDeleteMutation.error)
                  .message
              }
            </p>
          ) : null}
        </DialogContent>
      </Dialog>
    </section>
  )
}
