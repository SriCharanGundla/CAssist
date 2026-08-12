import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  RiArrowLeftSLine,
  RiCheckboxCircleLine,
  RiEyeLine,
  RiEyeOffLine,
} from "@remixicon/react"
import { Link, useParams } from "react-router-dom"

import { SourcePreview } from "@/components/source-preview"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  correctResult,
  createOriginalViewUrl,
  downloadTallyExport,
  getResult,
  updateResultReview,
} from "@/lib/api"

const REVIEW_LABELS = {
  unreviewed: "Not reviewed",
  in_review: "In review",
  approved: "Approved",
}

function documentTypeLabel(value) {
  return value
    .split("_")
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ")
}

function CopyValue({ label, value }) {
  const [feedback, setFeedback] = React.useState("")
  const [open, setOpen] = React.useState(false)
  const timer = React.useRef(null)
  React.useEffect(() => () => window.clearTimeout(timer.current), [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setFeedback("Copied")
    } catch {
      setFeedback("Could not copy")
    }
    setOpen(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setOpen(false), 1200)
  }

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger
        aria-label={`Copy ${label}: ${value}`}
        className="cursor-pointer text-left text-sm font-medium break-words transition-colors hover:text-muted-foreground"
        onClick={copy}
        title="Click to copy"
      >
        {value}
      </PopoverTrigger>
      <PopoverContent
        className="flex w-auto items-center gap-1.5 px-2.5 py-1.5"
        side="top"
      >
        {feedback === "Copied" ? (
          <RiCheckboxCircleLine aria-hidden="true" className="size-4" />
        ) : null}
        <span>{feedback}</span>
      </PopoverContent>
    </Popover>
  )
}

function QualityIssues({ issues, onUseSuggestion, saving }) {
  if (!issues.length) return null
  return (
    <ul className="mt-2 space-y-2">
      {issues.map((issue) => (
        <li
          className="rounded-lg bg-destructive/10 px-3 py-2 text-xs"
          key={issue.code}
        >
          <p className="text-destructive">{issue.message}</p>
          {issue.suggested_value !== null ? (
            <Button
              className="mt-2 h-7 px-2 text-xs"
              disabled={saving}
              onClick={() => onUseSuggestion(issue.suggested_value)}
              variant="outline"
            >
              Use “{issue.suggested_value}”
            </Button>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

function EditableValue({
  issues,
  hideLabel = false,
  label,
  onSave,
  pageNumber,
  saving,
  targetId,
  value,
}) {
  const [editing, setEditing] = React.useState(false)
  const [draft, setDraft] = React.useState(value)
  const [reason, setReason] = React.useState("")

  const save = async (nextValue = draft, nextReason = reason) => {
    try {
      await onSave(targetId, nextValue, nextReason)
      setEditing(false)
      setReason("")
    } catch {
      // The page-level mutation error remains visible while this editor stays open.
    }
  }

  return (
    <div className="border-b py-4 last:border-b-0">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {!hideLabel ? (
              <p className="text-xs font-medium text-muted-foreground">
                {label}
              </p>
            ) : null}
            {pageNumber ? (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                Page {pageNumber}
              </span>
            ) : null}
          </div>
          {editing ? (
            <>
              <textarea
                aria-label={label}
                className="mt-2 min-h-10 w-full rounded-md border bg-background px-3 py-2 text-sm"
                disabled={saving}
                maxLength={20000}
                onChange={(event) => setDraft(event.target.value)}
                value={draft}
              />
              <input
                aria-label={`Reason for changing ${label}`}
                className="mt-2 h-9 w-full rounded-md border bg-background px-3 text-sm"
                disabled={saving}
                maxLength={1000}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Reason for correction (optional)"
                value={reason}
              />
            </>
          ) : (
            <div className="mt-1">
              <CopyValue label={label} value={value} />
            </div>
          )}
          <QualityIssues
            issues={issues}
            onUseSuggestion={(suggestion) =>
              save(suggestion, "Accepted quality-review suggestion")
            }
            saving={saving}
          />
        </div>
        {editing ? (
          <div className="flex gap-2">
            <Button disabled={saving || !draft} onClick={() => save()}>
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button
              disabled={saving}
              onClick={() => setEditing(false)}
              variant="ghost"
            >
              Cancel
            </Button>
          </div>
        ) : (
          <Button
            onClick={() => {
              setDraft(value)
              setEditing(true)
            }}
            variant="outline"
          >
            Edit
          </Button>
        )}
      </div>
    </div>
  )
}

function Card({ children, description, title }) {
  return (
    <section className="rounded-2xl border bg-card p-5 shadow-sm">
      <h2 className="font-semibold">{title}</h2>
      {description ? (
        <p className="mt-1 text-xs text-muted-foreground">{description}</p>
      ) : null}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function CorrectionHistory({ corrections }) {
  if (!corrections.length)
    return (
      <p className="mt-3 text-sm text-muted-foreground">No corrections yet.</p>
    )
  return (
    <ol className="mt-3 space-y-3">
      {corrections.map((correction) => (
        <li className="rounded-lg border p-3 text-xs" key={correction.id}>
          <p className="font-mono text-muted-foreground">
            {correction.target_id}
          </p>
          <p className="mt-1 break-words">
            {String(correction.previous_value)} →{" "}
            <span className="font-medium">
              {String(correction.corrected_value)}
            </span>
          </p>
          {correction.reason ? (
            <p className="mt-1 text-muted-foreground">{correction.reason}</p>
          ) : null}
        </li>
      ))}
    </ol>
  )
}

export function ReviewPage() {
  const { resultId } = useParams()
  const queryClient = useQueryClient()
  const [showOriginal, setShowOriginal] = React.useState(true)

  const resultQuery = useQuery({
    queryKey: ["result", resultId],
    queryFn: ({ signal }) => getResult(resultId, { signal }),
  })
  const updateCachedResult = (result) =>
    queryClient.setQueryData(["result", resultId], result)
  const handleMutationError = (error) => {
    if (error.status === 409)
      queryClient.invalidateQueries({ queryKey: ["result", resultId] })
  }
  const correctionMutation = useMutation({
    mutationFn: ({ expectedVersion, reason, targetId, value }) =>
      correctResult(resultId, expectedVersion, [
        { target_id: targetId, value, reason: reason.trim() || null },
      ]),
    onSuccess: updateCachedResult,
    onError: handleMutationError,
  })
  const reviewMutation = useMutation({
    mutationFn: ({ expectedVersion, status }) =>
      updateResultReview(resultId, expectedVersion, status),
    onSuccess: updateCachedResult,
    onError: handleMutationError,
  })
  const exportMutation = useMutation({
    mutationFn: ({ expectedVersion }) =>
      downloadTallyExport(resultId, expectedVersion),
    onError: handleMutationError,
  })
  const sourceMutation = useMutation({
    mutationFn: createOriginalViewUrl,
  })
  const requestSource = sourceMutation.mutate
  const sourceDocumentId = resultQuery.data?.document_id
  const sourceAvailable = resultQuery.data?.original_available
  React.useEffect(() => {
    if (
      showOriginal &&
      sourceAvailable &&
      sourceDocumentId &&
      !sourceMutation.data &&
      !sourceMutation.error &&
      !sourceMutation.isPending
    ) {
      requestSource(sourceDocumentId)
    }
  }, [
    requestSource,
    showOriginal,
    sourceAvailable,
    sourceDocumentId,
    sourceMutation.data,
    sourceMutation.error,
    sourceMutation.isPending,
  ])

  if (resultQuery.isPending)
    return <p className="text-sm text-muted-foreground">Loading extraction…</p>
  if (resultQuery.error)
    return (
      <p className="text-sm text-destructive" role="alert">
        {resultQuery.error.message}
      </p>
    )

  const result = resultQuery.data
  const data = result.effective_data
  const issuesByTarget = result.quality_issues.reduce((grouped, issue) => {
    grouped.set(issue.target_id, [
      ...(grouped.get(issue.target_id) || []),
      issue,
    ])
    return grouped
  }, new Map())
  const knownTargets = new Set([
    ...data.fields.map((field) => field.id),
    ...data.tables.flatMap((table) =>
      table.rows.flatMap((row) => row.cells.map((cell) => cell.id))
    ),
    ...data.text_blocks.map((block) => block.id),
  ])
  const unmappedIssues = result.quality_issues.filter(
    (issue) => !knownTargets.has(issue.target_id)
  )
  const mutationError =
    correctionMutation.error || reviewMutation.error || exportMutation.error
  const saveCorrection = (targetId, value, reason) => {
    correctionMutation.reset()
    return correctionMutation.mutateAsync({
      expectedVersion: result.version,
      targetId,
      value,
      reason,
    })
  }
  const extractedCount =
    data.fields.length +
    data.text_blocks.length +
    data.tables.reduce(
      (count, table) =>
        count +
        table.rows.reduce((rowCount, row) => rowCount + row.cells.length, 0),
      0
    )
  const editableProps = (targetId) => ({
    issues: issuesByTarget.get(targetId) || [],
    onSave: saveCorrection,
    saving: correctionMutation.isPending,
    targetId,
  })

  return (
    <section className="mx-auto max-w-7xl">
      <Button
        className="mb-5 -ml-2"
        nativeButton={false}
        render={<Link to={`/documents/${result.document_id}`} />}
        variant="ghost"
      >
        <RiArrowLeftSLine /> Back
      </Button>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Review extracted document
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Version {result.version} · {REVIEW_LABELS[result.review_status]}
          </p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
            <span className="rounded-full border bg-card px-3 py-1">
              {documentTypeLabel(result.document_type)}
            </span>
            <span className="rounded-full border bg-card px-3 py-1">
              {extractedCount} extracted values
            </span>
            <span className="rounded-full border bg-card px-3 py-1">
              {result.quality_issues.length} need attention
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {result.original_available ? (
            <Button
              onClick={() => setShowOriginal((current) => !current)}
              variant="outline"
            >
              {showOriginal ? <RiEyeOffLine /> : <RiEyeLine />}
              {showOriginal ? "Hide original" : "Show original"}
            </Button>
          ) : null}
          {result.review_status === "approved" ? (
            <>
              <Button
                disabled={exportMutation.isPending}
                onClick={() =>
                  exportMutation.mutate({ expectedVersion: result.version })
                }
              >
                {exportMutation.isPending
                  ? "Creating export…"
                  : "Download Tally JSON"}
              </Button>
              <Button
                disabled={reviewMutation.isPending}
                onClick={() =>
                  reviewMutation.mutate({
                    expectedVersion: result.version,
                    status: "in_review",
                  })
                }
                variant="outline"
              >
                Return to review
              </Button>
            </>
          ) : (
            <Button
              disabled={
                reviewMutation.isPending || correctionMutation.isPending
              }
              onClick={() =>
                reviewMutation.mutate({
                  expectedVersion: result.version,
                  status: "approved",
                })
              }
            >
              {reviewMutation.isPending ? "Approving…" : "Approve extraction"}
            </Button>
          )}
        </div>
      </div>

      <div className="mt-4">
        {unmappedIssues.map((issue) => (
          <p
            className="mt-2 text-sm text-destructive"
            key={`${issue.target_id}-${issue.code}`}
          >
            {issue.message}
          </p>
        ))}
        {mutationError ? (
          <p className="mt-3 text-sm text-destructive" role="alert">
            {mutationError.message}
          </p>
        ) : null}
      </div>

      <div
        className={`mt-6 grid items-start gap-5 ${showOriginal && result.original_available ? "lg:grid-cols-2" : ""}`}
      >
        {showOriginal && result.original_available ? (
          <SourcePreview
            error={sourceMutation.error}
            loading={sourceMutation.isPending}
            mimeType={result.original_mime_type}
            sourceUrl={sourceMutation.data?.url}
          />
        ) : null}
        <div
          className={
            showOriginal && result.original_available
              ? "space-y-5"
              : "grid items-start gap-5 lg:grid-cols-2"
          }
        >
          <div className="space-y-5">
            {data.fields.length ? (
              <Card title="Fields">
                {data.fields.map((field) => (
                  <EditableValue
                    key={field.id}
                    label={field.label}
                    pageNumber={field.page_number}
                    value={field.value}
                    {...editableProps(field.id)}
                  />
                ))}
              </Card>
            ) : null}
            {data.text_blocks.length ? (
              <Card
                description="Useful visible text that was not presented as a labelled field."
                title="Other text"
              >
                {data.text_blocks.map((block, index) => (
                  <EditableValue
                    key={block.id}
                    label={`Text block ${index + 1}`}
                    pageNumber={block.page_number}
                    value={block.text}
                    {...editableProps(block.id)}
                  />
                ))}
              </Card>
            ) : null}
          </div>

          <div className="space-y-5">
            {data.tables.map((table, tableIndex) => (
              <Card
                key={table.id}
                description={`Source pages: ${table.page_numbers.join(", ")}`}
                title={table.title || `Table ${tableIndex + 1}`}
              >
                <div className="overflow-x-auto">
                  <table className="w-full min-w-max text-left text-sm">
                    <thead>
                      <tr className="border-b">
                        {table.headers.map((header, index) => (
                          <th
                            className="px-2 py-2 text-xs font-medium text-muted-foreground"
                            key={`${header}-${index}`}
                          >
                            {header}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {table.rows.map((row) => (
                        <tr className="border-b last:border-b-0" key={row.id}>
                          {row.cells.map((cell, cellIndex) => (
                            <td
                              className="min-w-36 px-2 align-top"
                              key={cell.id}
                            >
                              <EditableValue
                                hideLabel
                                label={`${table.headers[cellIndex]}, row ${table.rows.indexOf(row) + 1}`}
                                value={cell.value}
                                {...editableProps(cell.id)}
                              />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            ))}
            {!data.fields.length &&
            !data.tables.length &&
            !data.text_blocks.length ? (
              <Card title="No visible values extracted">
                <p className="text-sm text-muted-foreground">
                  Review the original and retry processing if this document
                  contains readable information.
                </p>
              </Card>
            ) : null}
            <Card title="Correction history">
              <CorrectionHistory corrections={result.corrections} />
            </Card>
          </div>
        </div>
      </div>
    </section>
  )
}
