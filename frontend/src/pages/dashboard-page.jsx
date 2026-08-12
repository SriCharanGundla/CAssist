import * as React from "react"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import {
  RiDeleteBinLine,
  RiEditLine,
  RiExternalLinkLine,
  RiRestartLine,
} from "@remixicon/react"
import { Link, useLocation } from "react-router-dom"

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
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  createOriginalViewUrl,
  deleteDocumentOriginal,
  getRun,
  listDocuments,
  permanentlyDeleteDocument,
  retryDocumentProcessing,
} from "@/lib/api"

const TERMINAL_RUN_STATUSES = new Set(["succeeded", "failed", "cancelled"])

const STATUS_LABELS = {
  upload_pending: "Upload pending",
  uploaded: "Queued",
  processing: "Processing",
  ready: "Extraction complete",
  failed: "Processing failed",
  queued: "Queued",
  preprocessing: "Preparing",
  preparing: "Preparing",
  classifying: "Classifying",
  extracting: "Extracting",
  quality_check: "Checking quality",
  validating: "Saving",
  saving: "Saving",
  complete: "Extraction complete",
  succeeded: "Extraction complete",
  cancelled: "Processing failed",
}

function statusBadgeClass(status) {
  if (["ready", "succeeded", "complete"].includes(status)) {
    return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
  }
  if (["failed", "cancelled"].includes(status)) {
    return "bg-destructive/15 text-destructive"
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

function DocumentRow({ document }) {
  const queryClient = useQueryClient()
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false)
  const runId = document.latest_run?.id
  const initialRun = document.latest_run
  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: ({ signal }) => getRun(runId, { signal }),
    enabled: Boolean(runId && !TERMINAL_RUN_STATUSES.has(initialRun.status)),
    refetchInterval: (query) =>
      query.state.data && TERMINAL_RUN_STATUSES.has(query.state.data.status)
        ? false
        : 2_000,
  })
  const run = runQuery.data || initialRun
  const isRunning = Boolean(run && !TERMINAL_RUN_STATUSES.has(run.status))
  const deleteDisabled =
    isRunning ||
    ["upload_pending", "uploaded", "processing"].includes(document.status)
  const displayStatus =
    runQuery.data?.progress?.stage || run?.status || document.status
  const statusText = `${STATUS_LABELS[displayStatus] || displayStatus.replaceAll("_", " ")}${isRunning ? "…" : ""}`
  const resultId = run?.result_id

  const refreshDocuments = () =>
    queryClient.invalidateQueries({ queryKey: ["documents"] })
  const viewMutation = useMutation({
    mutationFn: () => createOriginalViewUrl(document.id),
    onSuccess: ({ url }) => window.open(url, "_blank", "noopener,noreferrer"),
  })
  const retryMutation = useMutation({
    mutationFn: () => retryDocumentProcessing(document.id),
    onSuccess: refreshDocuments,
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
    originalDeleteMutation.error ||
    permanentDeleteMutation.error
  const deleting =
    originalDeleteMutation.isPending || permanentDeleteMutation.isPending

  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-5 py-4 transition-colors hover:bg-muted/50">
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
        {actionError ? (
          <p className="mt-1 text-xs text-destructive" role="alert">
            {actionError.message}
          </p>
        ) : null}
      </div>

      <div className="flex items-center gap-1">
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
        <IconAction
          disabled={deleteDisabled}
          label="Delete document"
          onClick={() => setDeleteDialogOpen(true)}
          variant="ghost"
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
    </li>
  )
}

export function DashboardPage() {
  const location = useLocation()
  const documentsQuery = useInfiniteQuery({
    queryKey: ["documents"],
    queryFn: ({ pageParam, signal }) =>
      listDocuments({ cursor: pageParam, limit: 10, signal }),
    initialPageParam: null,
    getNextPageParam: (page) => page.next_cursor || undefined,
    refetchInterval: (query) => {
      const documents =
        query.state.data?.pages.flatMap((page) => page.items) || []
      return documents.some((document) =>
        ["upload_pending", "uploaded", "processing"].includes(document.status)
      )
        ? 2_000
        : false
    },
  })
  const documents =
    documentsQuery.data?.pages.flatMap((page) => page.items) || []

  return (
    <TooltipProvider>
      <section>
        {location.state?.deleted ? (
          <p className="mb-5 rounded-lg bg-muted p-3 text-sm" role="status">
            Document and extraction data deleted.
          </p>
        ) : null}
        {location.state?.deduplicated ? (
          <p className="mb-5 rounded-lg bg-muted p-3 text-sm" role="status">
            Existing document and processing history reused.
          </p>
        ) : location.state?.uploaded ? (
          <p className="mb-5 rounded-lg bg-muted p-3 text-sm" role="status">
            Document uploaded and queued for extraction.
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
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 border-b px-5 py-4">
            <h2 className="font-semibold">Recent documents</h2>
            <span className="text-xs font-medium text-muted-foreground">
              Actions
            </span>
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
                <DocumentRow document={document} key={document.id} />
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
    </TooltipProvider>
  )
}
