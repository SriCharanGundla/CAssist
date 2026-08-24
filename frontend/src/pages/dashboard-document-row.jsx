import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  RiDeleteBinLine,
  RiEditLine,
  RiExternalLinkLine,
  RiPlayCircleLine,
  RiRestartLine,
  RiStopFill,
  RiTestTubeLine,
} from "@remixicon/react"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  cancelProcessingRun,
  confirmDocumentProcessing,
  createOriginalViewUrl,
  deleteDocumentOriginal,
  getRun,
  permanentlyDeleteDocument,
  retryDocumentProcessing,
} from "@/lib/api"
import { adaptivePollingInterval } from "@/lib/polling"
import { documentDeletionDisabled } from "@/pages/dashboard-utils"

const TERMINAL_RUN_STATUSES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "needs_confirmation",
  "unsupported",
])

const STATUS_LABELS = {
  upload_pending: "Upload pending",
  uploaded: "Queued",
  processing: "Processing",
  ready: "Completed",
  failed: "Failed",
  queued: "Queued",
  preprocessing: "Preparing",
  preparing: "Preparing",
  classifying: "Classifying",
  extracting: "Extracting",
  organizing: "Organizing",
  quality_check: "Checking quality",
  validating: "Saving",
  saving: "Saving",
  stopping: "Stopping",
  complete: "Completed",
  succeeded: "Completed",
  cancelled: "Cancelled",
  needs_confirmation: "Confirmation needed",
  unsupported: "Unsupported document",
}

function statusBadgeClass(status) {
  if (["ready", "succeeded", "complete"].includes(status)) {
    return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
  }
  if (["failed", "cancelled"].includes(status)) {
    return "bg-destructive/15 text-destructive"
  }
  if (status === "unsupported") {
    return "bg-destructive/15 text-destructive"
  }
  if (status === "needs_confirmation") {
    return "bg-amber-500/15 text-amber-700 dark:text-amber-400"
  }
  return "bg-secondary text-secondary-foreground"
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

function formatDate(value) {
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}

function formatEstimatedCost(value) {
  if (value === null || value === undefined) return null
  return `$${Number(value).toFixed(6)}`
}

function IconAction({ label, ...buttonProps }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={<Button aria-label={label} size="icon" {...buttonProps} />}
      />
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

export function DocumentListSkeleton() {
  return (
    <div aria-label="Loading documents" className="divide-y">
      {Array.from({ length: 3 }, (_, index) => (
        <div
          className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-5 py-4"
          key={index}
        >
          <div className="space-y-2 motion-safe:animate-pulse">
            <div className="h-4 w-2/3 rounded bg-muted" />
            <div className="h-3 w-28 rounded bg-muted" />
          </div>
          <div className="size-8 rounded-full bg-muted motion-safe:animate-pulse" />
        </div>
      ))}
    </div>
  )
}

export function DocumentRow({
  bulkDeleting,
  document,
  onSelectionChange,
  selected,
  selectionMode,
}) {
  const queryClient = useQueryClient()
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false)
  const [confirmDialogOpen, setConfirmDialogOpen] = React.useState(false)
  const runId = document.latest_run?.id
  const initialRun = document.latest_run
  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: ({ signal }) => getRun(runId, { signal }),
    enabled: Boolean(runId && !TERMINAL_RUN_STATUSES.has(initialRun.status)),
    refetchInterval: (query) =>
      adaptivePollingInterval(query, TERMINAL_RUN_STATUSES),
  })
  const run = runQuery.data || initialRun
  const isRunning = Boolean(run && !TERMINAL_RUN_STATUSES.has(run.status))
  const isStopping = Boolean(isRunning && run?.cancellation_requested_at)
  const deleteDisabled = documentDeletionDisabled(document, run)
  const displayStatus =
    runQuery.data?.progress?.stage || run?.status || document.status
  const statusText = `${STATUS_LABELS[displayStatus] || displayStatus.replaceAll("_", " ")}${isRunning ? "…" : ""}`
  const resultId = run?.result_id

  const refreshDocuments = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["documents"] }),
      queryClient.invalidateQueries({ queryKey: ["storage-quota"] }),
    ])
  const viewMutation = useMutation({
    mutationFn: () => createOriginalViewUrl(document.id),
    onSuccess: ({ url }) => window.open(url, "_blank", "noopener,noreferrer"),
  })
  const retryMutation = useMutation({
    mutationFn: () => retryDocumentProcessing(document.id),
    onSuccess: refreshDocuments,
  })
  const confirmMutation = useMutation({
    mutationFn: () => confirmDocumentProcessing(document.id),
    onSuccess: async () => {
      setConfirmDialogOpen(false)
      await refreshDocuments()
      toast.success("Document confirmed and queued for extraction.")
    },
  })
  const cancelMutation = useMutation({
    mutationFn: () => cancelProcessingRun(runId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["run", runId] }),
  })
  const originalDeleteMutation = useMutation({
    mutationFn: () => deleteDocumentOriginal(document.id),
    onSuccess: async () => {
      setDeleteDialogOpen(false)
      await refreshDocuments()
    },
  })
  const permanentDeleteMutation = useMutation({
    mutationFn: () => permanentlyDeleteDocument(document.id),
    onSuccess: async () => {
      setDeleteDialogOpen(false)
      await refreshDocuments()
    },
  })
  const actionError =
    viewMutation.error ||
    retryMutation.error ||
    confirmMutation.error ||
    cancelMutation.error ||
    originalDeleteMutation.error ||
    permanentDeleteMutation.error
  const deleting =
    originalDeleteMutation.isPending || permanentDeleteMutation.isPending

  return (
    <li
      className={`grid items-center gap-4 px-5 py-4 transition-colors hover:bg-muted/50 ${selectionMode ? "grid-cols-[auto_minmax(0,1fr)_auto]" : "grid-cols-[minmax(0,1fr)_auto]"}`}
    >
      {selectionMode ? (
        <Checkbox
          aria-label={`Select ${document.original_filename}`}
          checked={selected}
          disabled={deleteDisabled || bulkDeleting}
          onCheckedChange={onSelectionChange}
        />
      ) : null}
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {document.original_available ? (
            <button
              aria-label={`Open ${document.original_filename} in a new tab`}
              className="group flex min-w-0 items-center gap-1.5 text-left text-sm font-medium"
              disabled={viewMutation.isPending}
              onClick={() => viewMutation.mutate()}
              type="button"
            >
              <span className="truncate group-hover:underline group-focus-visible:underline">
                {document.original_filename}
              </span>
              <RiExternalLinkLine className="size-3.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
            </button>
          ) : (
            <span className="truncate text-sm font-medium">
              {document.original_filename}
            </span>
          )}
          <span className="shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium">
            {fileTypeLabel(document.mime_type)}
          </span>
          <span
            className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ${statusBadgeClass(displayStatus)}`}
          >
            {isRunning ? (
              <span
                aria-hidden="true"
                className="size-2.5 animate-spin rounded-full border-2 border-current/30 border-t-current"
              />
            ) : null}
            {statusText}
          </span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {formatDate(document.created_at)}
        </p>
        {run?.estimated_cost_usd !== null &&
        run?.estimated_cost_usd !== undefined ? (
          <p className="mt-1 text-xs text-muted-foreground">
            Estimated model cost {formatEstimatedCost(run.estimated_cost_usd)} ·{" "}
            {(run.input_tokens ?? 0) + (run.output_tokens ?? 0)} tokens
          </p>
        ) : null}
        {actionError ? (
          <p className="mt-1 text-xs text-destructive" role="alert">
            {actionError.message}
          </p>
        ) : null}
      </div>

      <div className="flex items-center gap-1">
        {import.meta.env.DEV && document.original_available ? (
          <IconAction
            label="Compare models"
            nativeButton={false}
            render={<Link to={`/dev/compare/${document.id}`} />}
            variant="ghost"
          >
            <RiTestTubeLine />
          </IconAction>
        ) : null}
        {resultId ? (
          <IconAction
            label="Review extraction"
            nativeButton={false}
            render={<Link to={`/results/${resultId}/review`} />}
            variant="ghost"
          >
            <RiEditLine />
          </IconAction>
        ) : null}
        {run?.status === "failed" && document.original_available ? (
          <IconAction
            disabled={retryMutation.isPending}
            label="Retry extraction"
            onClick={() => retryMutation.mutate()}
            variant="ghost"
          >
            <RiRestartLine />
          </IconAction>
        ) : null}
        {run?.status === "needs_confirmation" && document.original_available ? (
          <IconAction
            disabled={confirmMutation.isPending}
            label="Confirm document processing"
            onClick={() => setConfirmDialogOpen(true)}
            variant="ghost"
          >
            <RiPlayCircleLine />
          </IconAction>
        ) : null}
        {isRunning ? (
          <IconAction
            disabled={cancelMutation.isPending || isStopping}
            label={isStopping ? "Stopping processing" : "Stop processing"}
            onClick={() => cancelMutation.mutate()}
            variant="destructive"
          >
            <RiStopFill />
          </IconAction>
        ) : null}
        <IconAction
          disabled={deleteDisabled || bulkDeleting}
          label="Delete document"
          onClick={() => setDeleteDialogOpen(true)}
          variant="destructive"
        >
          <RiDeleteBinLine />
        </IconAction>
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
                disabled={deleting}
                onClick={() => originalDeleteMutation.mutate()}
                variant="outline"
              >
                {originalDeleteMutation.isPending
                  ? "Deleting…"
                  : "Delete File, Keep Data"}
              </Button>
            ) : null}
            <Button
              disabled={deleting}
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
        </DialogContent>
      </Dialog>

      <Dialog onOpenChange={setConfirmDialogOpen} open={confirmDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Process this document anyway?</DialogTitle>
            <DialogDescription>
              CAssist could not confidently verify that this is a financial or
              professional document. Continuing will use the extraction model.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose render={<Button variant="ghost" />}>
              Cancel
            </DialogClose>
            <Button
              disabled={confirmMutation.isPending}
              onClick={() => confirmMutation.mutate()}
            >
              {confirmMutation.isPending ? "Queuing…" : "Process anyway"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </li>
  )
}
