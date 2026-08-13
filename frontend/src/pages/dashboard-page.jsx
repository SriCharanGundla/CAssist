import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  RiDeleteBinLine,
  RiEditLine,
  RiExternalLinkLine,
  RiRestartLine,
  RiStopFill,
  RiTestTubeLine,
} from "@remixicon/react"
import { Link, useLocation, useNavigate } from "react-router-dom"
import { toast } from "sonner"

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
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  cancelProcessingRun,
  createOriginalViewUrl,
  deleteDocumentOriginal,
  getRun,
  listDocuments,
  permanentlyDeleteDocument,
  retryDocumentProcessing,
} from "@/lib/api"
import { adaptivePollingInterval } from "@/lib/polling"

const TERMINAL_RUN_STATUSES = new Set(["succeeded", "failed", "cancelled"])
const DOCUMENT_TYPE_OPTIONS = [
  ["tax_invoice", "Tax invoice"],
  ["invoice", "Invoice"],
  ["receipt", "Receipt"],
  ["credit_note", "Credit note"],
  ["debit_note", "Debit note"],
  ["cheque", "Cheque"],
  ["bank_statement", "Bank statement"],
  ["other_financial_document", "Other financial document"],
]
const STATUS_FILTER_OPTIONS = [
  ["upload_pending", "Upload pending"],
  ["uploaded", "Queued"],
  ["processing", "Processing"],
  ["ready", "Ready"],
  ["failed", "Failed"],
]

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
  organizing: "Organizing",
  quality_check: "Checking quality",
  validating: "Saving",
  saving: "Saving",
  stopping: "Stopping",
  complete: "Extraction complete",
  succeeded: "Extraction complete",
  cancelled: "Cancelled",
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

function DocumentListSkeleton() {
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
      adaptivePollingInterval(query, TERMINAL_RUN_STATUSES),
  })
  const run = runQuery.data || initialRun
  const isRunning = Boolean(run && !TERMINAL_RUN_STATUSES.has(run.status))
  const isStopping = Boolean(isRunning && run?.cancellation_requested_at)
  const deleteDisabled = run
    ? isRunning
    : ["upload_pending", "uploaded", "processing"].includes(document.status)
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
    cancelMutation.error ||
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
          disabled={deleteDisabled}
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
    </li>
  )
}

export function DashboardPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const [search, setSearch] = React.useState("")
  const [debouncedSearch, setDebouncedSearch] = React.useState("")
  const [statusFilter, setStatusFilter] = React.useState("")
  const [documentTypeFilter, setDocumentTypeFilter] = React.useState("")
  const [pageIndex, setPageIndex] = React.useState(0)
  const [pageCursors, setPageCursors] = React.useState([null])
  const resetPagination = React.useCallback(() => {
    setPageIndex(0)
    setPageCursors([null])
  }, [])
  React.useEffect(() => {
    const timeout = window.setTimeout(() => {
      const nextSearch = search.trim()
      if (nextSearch === debouncedSearch) return
      setDebouncedSearch(nextSearch)
      resetPagination()
    }, 250)
    return () => window.clearTimeout(timeout)
  }, [debouncedSearch, resetPagination, search])
  React.useEffect(() => {
    if (location.state?.deleted) {
      toast.success("Document and extraction data deleted.")
    } else if (location.state?.deduplicated) {
      toast.info("Existing document and processing history reused.")
    } else if (location.state?.uploaded) {
      toast.success(
        location.state.uploadCount > 1
          ? `${location.state.uploadCount} documents uploaded and queued for extraction.`
          : "Document uploaded and queued for extraction."
      )
    } else {
      return
    }
    navigate(location.pathname, { replace: true, state: null })
  }, [location.pathname, location.state, navigate])
  const currentCursor = pageCursors[pageIndex]
  const documentsQuery = useQuery({
    queryKey: [
      "documents",
      currentCursor,
      debouncedSearch,
      documentTypeFilter,
      statusFilter,
    ],
    queryFn: ({ signal }) =>
      listDocuments({
        cursor: currentCursor,
        documentType: documentTypeFilter || undefined,
        limit: 10,
        search: debouncedSearch || undefined,
        signal,
        status: statusFilter || undefined,
      }),
    staleTime: 5_000,
  })
  const documents = documentsQuery.data?.items || []
  const hasPreviousPage = pageIndex > 0
  const nextCursor = documentsQuery.data?.next_cursor
  const hasNextPage = Boolean(nextCursor)

  const showPreviousPage = (event) => {
    event.preventDefault()
    setPageIndex((current) => Math.max(0, current - 1))
  }

  const showNextPage = (event) => {
    event.preventDefault()
    if (!nextCursor) return
    setPageCursors((current) => {
      if (current[pageIndex + 1] === nextCursor) return current
      return [...current.slice(0, pageIndex + 1), nextCursor]
    })
    setPageIndex((current) => current + 1)
  }

  return (
    <TooltipProvider>
      <section>
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
            Upload
          </Button>
        </div>

        <div className="mt-7 overflow-hidden rounded-2xl border bg-card shadow-sm">
          <div className="border-b px-5 py-4">
            <h2 className="font-semibold">Recent documents</h2>
            <div className="mt-4 grid gap-2 sm:grid-cols-[minmax(12rem,1fr)_auto_auto]">
              <input
                aria-label="Search documents"
                className="h-9 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search filenames"
                type="search"
                value={search}
              />
              <select
                aria-label="Filter by status"
                className="h-9 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
                onChange={(event) => {
                  setStatusFilter(event.target.value)
                  resetPagination()
                }}
                value={statusFilter}
              >
                <option value="">All statuses</option>
                {STATUS_FILTER_OPTIONS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <select
                aria-label="Filter by document type"
                className="h-9 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
                onChange={(event) => {
                  setDocumentTypeFilter(event.target.value)
                  resetPagination()
                }}
                value={documentTypeFilter}
              >
                <option value="">All document types</option>
                {DOCUMENT_TYPE_OPTIONS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 border-b bg-muted/30 px-5 py-2 text-xs font-medium text-muted-foreground">
            <span>Document</span>
            <span>Actions</span>
          </div>
          {documentsQuery.isPending ? (
            <DocumentListSkeleton />
          ) : documentsQuery.error ? (
            <div className="p-5">
              <p className="text-sm text-destructive" role="alert">
                {documentsQuery.error.message}
              </p>
              <Button
                className="mt-3"
                onClick={() => documentsQuery.refetch()}
                variant="outline"
              >
                Retry
              </Button>
            </div>
          ) : documents.length ? (
            <ul className="divide-y">
              {documents.map((document) => (
                <DocumentRow document={document} key={document.id} />
              ))}
            </ul>
          ) : (
            <div className="p-8 text-center">
              <p className="text-sm font-medium">
                {debouncedSearch || statusFilter || documentTypeFilter
                  ? "No matching documents"
                  : "No documents yet"}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {debouncedSearch || statusFilter || documentTypeFilter
                  ? "Adjust the search or filters and try again."
                  : "Upload documents to start the review workflow."}
              </p>
            </div>
          )}
          {hasPreviousPage || hasNextPage ? (
            <Pagination className="border-t p-4">
              <PaginationContent>
                {hasPreviousPage ? (
                  <PaginationItem>
                    <PaginationPrevious href="#" onClick={showPreviousPage} />
                  </PaginationItem>
                ) : null}
                <PaginationItem>
                  <PaginationLink
                    href="#"
                    isActive
                    onClick={(event) => event.preventDefault()}
                  >
                    {pageIndex + 1}
                  </PaginationLink>
                </PaginationItem>
                {hasNextPage ? (
                  <PaginationItem>
                    <PaginationNext href="#" onClick={showNextPage} />
                  </PaginationItem>
                ) : null}
              </PaginationContent>
            </Pagination>
          ) : null}
        </div>
      </section>
    </TooltipProvider>
  )
}
