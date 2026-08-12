import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import {
  correctResult,
  downloadTallyExport,
  getResult,
  updateResultReview,
} from "@/lib/api"
import {
  INVOICE_FIELDS,
  LINE_ITEM_FIELDS,
  LINE_TAX_FIELDS,
  PARTY_FIELDS,
  TOTAL_FIELDS,
  valueAtPointer,
} from "@/pages/review-fields"

const REVIEW_LABELS = {
  unreviewed: "Not reviewed",
  in_review: "In review",
  approved: "Approved",
}

function displayValue(value) {
  if (value === null || value === undefined || value === "") {
    return "Not extracted"
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No"
  }
  return String(value)
}

function inputValue(value, type) {
  if (type === "boolean") {
    return value === null || value === undefined ? "" : String(value)
  }
  return value ?? ""
}

function correctedValue(draft, type) {
  if (type === "boolean") {
    if (draft === "") return null
    return draft === "true"
  }
  return draft === "" ? null : draft
}

function hasValue(value) {
  if (Array.isArray(value)) return value.length > 0
  return value !== null && value !== undefined && value !== ""
}

function documentTypeLabel(value) {
  return value
    .split("_")
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ")
}

function FieldIssues({ issues }) {
  if (!issues.length) return null
  return (
    <ul className="mt-2 space-y-1">
      {issues.map((issue) => (
        <li
          className="text-xs text-destructive"
          key={`${issue.code}-${issue.field_path}`}
        >
          {issue.message}
        </li>
      ))}
    </ul>
  )
}

function EditableField({ evidence, field, issues, onSave, saving, value }) {
  const [editing, setEditing] = React.useState(false)
  const [draft, setDraft] = React.useState(() => inputValue(value, field.type))
  const [reason, setReason] = React.useState("")

  const save = async () => {
    try {
      await onSave(field.path, correctedValue(draft, field.type), reason)
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
            <p className="text-xs font-medium text-muted-foreground">
              {field.label}
            </p>
            {evidence ? (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                Source page {evidence.page_number}
              </span>
            ) : null}
          </div>
          {!editing ? (
            <p className="mt-1 text-sm font-medium break-words">
              {displayValue(value)}
            </p>
          ) : field.type === "boolean" ? (
            <select
              aria-label={field.label}
              className="mt-2 h-9 w-full rounded-md border bg-background px-3 text-sm"
              disabled={saving}
              onChange={(event) => setDraft(event.target.value)}
              value={draft}
            >
              <option value="">Not extracted</option>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          ) : field.multiline ? (
            <textarea
              aria-label={field.label}
              className="mt-2 min-h-20 w-full rounded-md border bg-background px-3 py-2 text-sm"
              disabled={saving}
              onChange={(event) => setDraft(event.target.value)}
              value={draft}
            />
          ) : (
            <input
              aria-label={field.label}
              className="mt-2 h-9 w-full rounded-md border bg-background px-3 text-sm"
              disabled={saving}
              onChange={(event) => setDraft(event.target.value)}
              value={draft}
            />
          )}
          <FieldIssues issues={issues} />
          {editing ? (
            <input
              aria-label={`Reason for changing ${field.label}`}
              className="mt-3 h-9 w-full rounded-md border bg-background px-3 text-sm"
              disabled={saving}
              maxLength={1000}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Reason for correction (optional)"
              value={reason}
            />
          ) : null}
        </div>
        {!editing ? (
          <Button
            onClick={() => {
              setDraft(inputValue(value, field.type))
              setEditing(true)
            }}
            variant="outline"
          >
            Edit
          </Button>
        ) : (
          <div className="flex gap-2">
            <Button disabled={saving} onClick={save}>
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
        )}
      </div>
    </div>
  )
}

function FieldSection({ children, description, title }) {
  return (
    <section className="rounded-2xl border bg-card p-5 shadow-sm">
      <h2 className="font-semibold">{title}</h2>
      {description ? (
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          {description}
        </p>
      ) : null}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function CorrectionHistory({ corrections }) {
  if (!corrections.length) {
    return (
      <p className="mt-3 text-sm text-muted-foreground">No corrections yet.</p>
    )
  }
  return (
    <ol className="mt-3 space-y-3">
      {corrections.map((correction) => (
        <li className="rounded-lg border p-3 text-xs" key={correction.id}>
          <p className="font-mono text-muted-foreground">
            {correction.field_path}
          </p>
          <p className="mt-1 break-words">
            {displayValue(correction.previous_value)} →{" "}
            <span className="font-medium">
              {displayValue(correction.corrected_value)}
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
  const resultQuery = useQuery({
    queryKey: ["result", resultId],
    queryFn: ({ signal }) => getResult(resultId, { signal }),
  })

  const updateCachedResult = (result) => {
    queryClient.setQueryData(["result", resultId], result)
  }
  const handleMutationError = (error) => {
    if (error.status === 409) {
      queryClient.invalidateQueries({ queryKey: ["result", resultId] })
    }
  }
  const correctionMutation = useMutation({
    mutationFn: ({ expectedVersion, path, reason, value }) =>
      correctResult(resultId, expectedVersion, [
        { field_path: path, value, reason: reason.trim() || null },
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
  const [showOptionalFields, setShowOptionalFields] = React.useState(false)

  if (resultQuery.isPending) {
    return <p className="text-sm text-muted-foreground">Loading extraction…</p>
  }
  if (resultQuery.error) {
    return (
      <p className="text-sm text-destructive" role="alert">
        {resultQuery.error.message}
      </p>
    )
  }

  const result = resultQuery.data
  const data = result.effective_data
  const issuesByPath = result.validation_issues.reduce((grouped, issue) => {
    const pathIssues = grouped.get(issue.field_path) || []
    grouped.set(issue.field_path, [...pathIssues, issue])
    return grouped
  }, new Map())
  const mutationError = correctionMutation.error || reviewMutation.error
  const evidenceByPath = new Map(
    (result.evidence || []).map((evidence) => [evidence.field_path, evidence])
  )
  const saveCorrection = (path, value, reason) => {
    correctionMutation.reset()
    return correctionMutation.mutateAsync({
      expectedVersion: result.version,
      path,
      value,
      reason,
    })
  }
  const renderField = (field) => (
    <EditableField
      evidence={
        evidenceByPath.get(field.path) ||
        [...evidenceByPath.entries()].find(([path]) =>
          field.path.startsWith(`${path}/`)
        )?.[1]
      }
      field={field}
      issues={issuesByPath.get(field.path) || []}
      key={field.path}
      onSave={saveCorrection}
      saving={correctionMutation.isPending}
      value={valueAtPointer(data, field.path)}
    />
  )
  const invoiceFields = INVOICE_FIELDS
  const partyFields = [
    ...PARTY_FIELDS.map((field) => ({
      ...field,
      group: "Supplier",
      path: `/supplier/${field.key}`,
    })),
    ...PARTY_FIELDS.map((field) => ({
      ...field,
      group: "Buyer",
      path: `/buyer/${field.key}`,
    })),
  ]
  const totalFields = TOTAL_FIELDS.map((field) => ({
    ...field,
    path: `/totals/${field.key}`,
  }))
  const scalarFields = [
    ...invoiceFields.map((field) => ({ ...field, group: "Invoice details" })),
    ...partyFields,
    ...totalFields.map((field) => ({ ...field, group: "Invoice totals" })),
  ]
  const isPrimaryField = (field) =>
    hasValue(valueAtPointer(data, field.path)) || issuesByPath.has(field.path)
  const optionalFields = scalarFields.filter((field) => !isPrimaryField(field))
  const extractedFieldCount =
    scalarFields.filter((field) => hasValue(valueAtPointer(data, field.path)))
      .length +
    data.line_items.reduce(
      (count, item) =>
        count +
        LINE_ITEM_FIELDS.filter((field) => hasValue(item[field.key])).length +
        LINE_TAX_FIELDS.filter((field) =>
          hasValue(item.tax_amounts[field.key])
        ).length,
      0
    )
  const mappedIssuePaths = new Set([
    ...scalarFields.map((field) => field.path),
    ...data.line_items.flatMap((_, index) => [
      ...LINE_ITEM_FIELDS.map(
        (field) => `/line_items/${index}/${field.key}`
      ),
      ...LINE_TAX_FIELDS.map(
        (field) => `/line_items/${index}/tax_amounts/${field.key}`
      ),
    ]),
  ])
  const unmappedIssues = result.validation_issues.filter(
    (issue) => !mappedIssuePaths.has(issue.field_path)
  )

  const renderSparseSection = (title, fields) => {
    const primaryFields = fields.filter(isPrimaryField)
    if (!primaryFields.length) return null
    return (
      <FieldSection key={title} title={title}>
        {primaryFields.map(renderField)}
      </FieldSection>
    )
  }

  return (
    <section className="mx-auto max-w-5xl">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
            Human review required
          </p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight">
            Review extracted invoice
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Version {result.version} · {REVIEW_LABELS[result.review_status]}
          </p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
            <span className="rounded-full border bg-card px-3 py-1">
              {documentTypeLabel(result.document_type)}
            </span>
            <span className="rounded-full border bg-card px-3 py-1">
              {extractedFieldCount} extracted fields
            </span>
            <span className="rounded-full border bg-card px-3 py-1">
              {result.validation_issues.length} need attention
            </span>
          </div>
        </div>
        <Button nativeButton={false} render={<Link to="/" />} variant="outline">
          Back to dashboard
        </Button>
      </div>

      <section className="mt-6 rounded-2xl border bg-card p-5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="font-semibold">
              {result.validation_issues.length
                ? "Needs attention"
                : "Ready for your review"}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Approval records your review; it does not submit bookkeeping or
              tax filings.
            </p>
          </div>
          {result.review_status === "approved" ? (
            <div className="flex flex-wrap gap-2">
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
            </div>
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
        {unmappedIssues.length ? (
          <ul className="mt-4 space-y-2">
            {unmappedIssues.map((issue) => (
              <li
                className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive"
                key={`${issue.code}-${issue.field_path}`}
              >
                {issue.message}
              </li>
            ))}
          </ul>
        ) : null}
        {mutationError || exportMutation.error ? (
          <p className="mt-4 text-sm text-destructive" role="alert">
            {(mutationError || exportMutation.error).message}
          </p>
        ) : null}
      </section>

      <div className="mt-6 grid gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          {renderSparseSection("Invoice details", invoiceFields)}
          {renderSparseSection(
            "Supplier",
            partyFields.filter((field) => field.group === "Supplier")
          )}
          {renderSparseSection(
            "Buyer",
            partyFields.filter((field) => field.group === "Buyer")
          )}
        </div>

        <div className="space-y-5">
          {renderSparseSection("Invoice totals", totalFields)}
          <FieldSection
            description="Descriptions and financial values are editable. Source pages remain extraction provenance."
            title={`Line items (${data.line_items.length})`}
          >
            {data.line_items.length ? (
              data.line_items.map((item, index) => (
                <div className="border-b py-4 last:border-b-0" key={index}>
                  <h3 className="text-sm font-semibold">Line {index + 1}</h3>
                  {LINE_ITEM_FIELDS.map((field) => ({
                    ...field,
                    path: `/line_items/${index}/${field.key}`,
                  }))
                    .filter(isPrimaryField)
                    .map(renderField)}
                  {LINE_TAX_FIELDS.some((field) =>
                    isPrimaryField({
                      ...field,
                      path: `/line_items/${index}/tax_amounts/${field.key}`,
                    })
                  ) ? (
                    <p className="mt-3 text-xs font-medium text-muted-foreground">
                      Tax amounts
                    </p>
                  ) : null}
                  {LINE_TAX_FIELDS.map((field) => ({
                    ...field,
                    path: `/line_items/${index}/tax_amounts/${field.key}`,
                  }))
                    .filter(isPrimaryField)
                    .map(renderField)}
                  <p className="mt-3 text-xs text-muted-foreground">
                    Source pages: {item.source_pages.join(", ") || "None"}
                  </p>
                </div>
              ))
            ) : (
              <p className="mt-3 text-sm text-muted-foreground">
                No line items were extracted.
              </p>
            )}
          </FieldSection>
          {optionalFields.length ? (
            <section className="rounded-2xl border border-dashed bg-card p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="font-semibold">Optional details</h2>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Absent fields stay out of the review unless you need them.
                  </p>
                </div>
                <Button
                  onClick={() => setShowOptionalFields((shown) => !shown)}
                  variant="outline"
                >
                  {showOptionalFields
                    ? "Hide optional details"
                    : `Add optional details (${optionalFields.length})`}
                </Button>
              </div>
              {showOptionalFields ? (
                <div className="mt-4">
                  {optionalFields.map((field, index) => (
                    <React.Fragment key={field.path}>
                      {index === 0 ||
                      optionalFields[index - 1].group !== field.group ? (
                        <p className="mt-4 text-xs font-semibold text-muted-foreground first:mt-0">
                          {field.group}
                        </p>
                      ) : null}
                      {renderField(field)}
                    </React.Fragment>
                  ))}
                </div>
              ) : null}
            </section>
          ) : null}
          <FieldSection title="Correction history">
            <CorrectionHistory corrections={result.corrections} />
          </FieldSection>
        </div>
      </div>
    </section>
  )
}
