import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { RiArrowLeftSLine, RiEyeLine, RiEyeOffLine } from "@remixicon/react"
import { Link, useParams } from "react-router-dom"
import { toast } from "sonner"

import { SourcePreview } from "@/components/source-preview"
import { Button } from "@/components/ui/button"
import {
  correctResult,
  createOriginalViewUrl,
  downloadTallyExport,
  getResult,
  updateResultReview,
  updateTallySelection,
} from "@/lib/api"
import {
  Card,
  CorrectionHistory,
  EditableValue,
  ReviewTable,
  SectionSelectionCheckbox,
} from "@/pages/review-components"

const REVIEW_LABELS = {
  unreviewed: "Not reviewed",
  in_review: "In review",
  approved: "Approved",
}
const SOURCE_VISIBILITY_STORAGE_KEY = "cassist-review-source-visible"

function documentTypeLabel(value) {
  return value
    .split("_")
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ")
}

function groupReviewSections(sections, tablesById) {
  const groups = []
  let compactSections = []
  const flushCompactSections = () => {
    if (!compactSections.length) return
    groups.push({
      id: `compact-${compactSections[0].id}`,
      sections: compactSections,
      type: "compact",
    })
    compactSections = []
  }
  for (const section of sections) {
    const containsTable = section.target_ids.some((targetId) =>
      tablesById.has(targetId)
    )
    if (containsTable) {
      flushCompactSections()
      groups.push({ id: `wide-${section.id}`, section, type: "wide" })
    } else {
      compactSections.push(section)
    }
  }
  flushCompactSections()
  return groups
}

export function ReviewPage() {
  const { resultId } = useParams()
  const queryClient = useQueryClient()
  const [showOriginal, setShowOriginal] = React.useState(
    () => localStorage.getItem(SOURCE_VISIBILITY_STORAGE_KEY) !== "false"
  )
  const [activeEvidence, setActiveEvidence] = React.useState(null)
  const [draftExcludedTargetIds, setDraftExcludedTargetIds] =
    React.useState(null)
  const toggleOriginal = () => {
    setShowOriginal((current) => {
      const next = !current
      localStorage.setItem(SOURCE_VISIBILITY_STORAGE_KEY, String(next))
      return next
    })
  }

  const resultQuery = useQuery({
    queryKey: ["result", resultId],
    queryFn: ({ signal }) => getResult(resultId, { signal }),
  })
  React.useEffect(() => {
    document.title = resultQuery.data?.original_filename
      ? `Review — ${resultQuery.data.original_filename} — CAssist`
      : "Review — CAssist"
  }, [resultQuery.data?.original_filename])
  const updateCachedResult = (result) =>
    queryClient.setQueryData(["result", resultId], result)
  const handleMutationError = (error) => {
    if (error.status === 409) {
      queryClient.invalidateQueries({ queryKey: ["result", resultId] })
      toast.info(
        "A newer version was loaded. Review your change and try again."
      )
    }
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
  const selectionMutation = useMutation({
    mutationFn: ({ excludedTargetIds, expectedVersion }) =>
      updateTallySelection(resultId, expectedVersion, excludedTargetIds),
    onSuccess: (result) => {
      setDraftExcludedTargetIds(null)
      updateCachedResult(result)
    },
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
      <div>
        <p className="text-sm text-destructive" role="alert">
          {resultQuery.error.message}
        </p>
        <Button
          className="mt-3"
          onClick={() => resultQuery.refetch()}
          variant="outline"
        >
          Retry
        </Button>
      </div>
    )

  const result = resultQuery.data
  const data = result.effective_data
  const fieldsById = new Map(data.fields.map((field) => [field.id, field]))
  const tablesById = new Map(data.tables.map((table) => [table.id, table]))
  const textBlocksById = new Map(
    data.text_blocks.map((block) => [block.id, block])
  )
  const editedTargets = new Set(
    result.corrections.map((correction) => correction.target_id)
  )
  const originalValues = new Map(
    result.extracted_data.fields.map((field) => [field.id, field.value])
  )
  for (const table of result.extracted_data.tables) {
    table.rows.forEach((row) => {
      row.cells.forEach((cell) => originalValues.set(cell.id, cell.value))
    })
  }
  result.extracted_data.text_blocks.forEach((block) => {
    originalValues.set(block.id, block.text)
  })
  const targetLabels = new Map(
    data.fields.map((field) => [field.id, field.label])
  )
  for (const table of data.tables) {
    table.rows.forEach((row, rowIndex) => {
      row.cells.forEach((cell, cellIndex) => {
        targetLabels.set(
          cell.id,
          `${table.headers[cellIndex]}, row ${rowIndex + 1}`
        )
      })
    })
  }
  data.text_blocks.forEach((block, index) => {
    targetLabels.set(block.id, `Text ${index + 1}`)
  })
  const presentationSections = result.presentation?.sections?.length
    ? result.presentation.sections
    : [
        {
          id: "section-0001",
          title: "Document details",
          target_ids: [
            ...data.fields.map((field) => field.id),
            ...data.tables.map((table) => table.id),
            ...data.text_blocks.map((block) => block.id),
          ],
        },
      ]
  const presentationTargetIds = presentationSections.flatMap(
    (section) => section.target_ids
  )
  const savedExcludedTargetIds = new Set(
    result.presentation?.excluded_target_ids || []
  )
  const effectiveExcludedTargetIds =
    draftExcludedTargetIds || savedExcludedTargetIds
  const orderedDraftExcludedTargetIds = presentationTargetIds.filter(
    (targetId) => effectiveExcludedTargetIds.has(targetId)
  )
  const selectionIsDirty =
    orderedDraftExcludedTargetIds.length !== savedExcludedTargetIds.size ||
    orderedDraftExcludedTargetIds.some(
      (targetId) => !savedExcludedTargetIds.has(targetId)
    )
  const selectedTargetCount =
    presentationTargetIds.length - orderedDraftExcludedTargetIds.length
  const approved = result.review_status === "approved"
  const sourcePaneVisible = showOriginal && result.original_available
  const visiblePresentationSections = approved
    ? presentationSections
        .map((section) => ({
          ...section,
          target_ids: section.target_ids.filter(
            (targetId) => !savedExcludedTargetIds.has(targetId)
          ),
        }))
        .filter((section) => section.target_ids.length)
    : presentationSections
  const targetOrder = presentationSections.flatMap((section) =>
    section.target_ids.flatMap((targetId) => {
      const table = tablesById.get(targetId)
      return table
        ? table.rows.flatMap((row) => row.cells.map((cell) => cell.id))
        : [targetId]
    })
  )
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
  const mutationError = [
    correctionMutation.error,
    reviewMutation.error,
    selectionMutation.error,
    exportMutation.error,
  ].find((error) => error && error.status !== 409)
  const saveCorrection = (targetId, value, reason) => {
    correctionMutation.reset()
    return correctionMutation.mutateAsync({
      expectedVersion: result.version,
      targetId,
      value,
      reason,
    })
  }
  const setTargetSelected = (targetId, selected) => {
    setDraftExcludedTargetIds((current) => {
      const next = new Set(current || savedExcludedTargetIds)
      if (selected) next.delete(targetId)
      else next.add(targetId)
      return next
    })
    selectionMutation.reset()
  }
  const setSectionSelected = (targetIds, selected) => {
    setDraftExcludedTargetIds((current) => {
      const next = new Set(current || savedExcludedTargetIds)
      targetIds.forEach((targetId) => {
        if (selected) next.delete(targetId)
        else next.add(targetId)
      })
      return next
    })
    selectionMutation.reset()
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
  const navigateTarget = (targetId, direction) => {
    const currentIndex = targetOrder.indexOf(targetId)
    const nextTarget = targetOrder[currentIndex + direction]
    if (!nextTarget) return
    window.requestAnimationFrame(() => {
      const button = document.querySelector(
        `[data-edit-target="${nextTarget}"]`
      )
      button?.focus()
      button?.click()
    })
  }
  const editableProps = (targetId, evidence) => ({
    canEdit: result.review_status !== "approved",
    edited: editedTargets.has(targetId),
    issues: issuesByTarget.get(targetId) || [],
    onSave: saveCorrection,
    onEvidenceChange: (nextEvidence) =>
      setActiveEvidence(nextEvidence === null ? null : evidence),
    onNavigate: navigateTarget,
    originalValue: originalValues.get(targetId),
    saving: correctionMutation.isPending,
    targetId,
  })
  const renderSectionCard = (section, className = "") => {
    const sectionSelectedCount = section.target_ids.filter(
      (targetId) => !effectiveExcludedTargetIds.has(targetId)
    ).length
    const selectionDisabled =
      result.review_status === "approved" || selectionMutation.isPending
    return (
      <Card
        className={className}
        collapsible
        headerControl={
          approved ? null : (
            <SectionSelectionCheckbox
              disabled={selectionDisabled}
              onChange={(selected) =>
                setSectionSelected(section.target_ids, selected)
              }
              selectedCount={sectionSelectedCount}
              targetCount={section.target_ids.length}
              title={section.title}
            />
          )
        }
        itemCount={section.target_ids.length}
        itemSummary={
          approved
            ? null
            : `${sectionSelectedCount} of ${section.target_ids.length} selected`
        }
        key={section.id}
        title={section.title}
      >
        {section.target_ids.map((targetId) => {
          const selected = !effectiveExcludedTargetIds.has(targetId)
          const field = fieldsById.get(targetId)
          if (field) {
            return (
              <EditableValue
                key={field.id}
                label={field.label}
                pageNumber={field.page_number}
                selected={selected}
                selectionDisabled={selectionDisabled}
                onSelectionChange={
                  approved
                    ? undefined
                    : (nextSelected) =>
                        setTargetSelected(field.id, nextSelected)
                }
                value={field.value}
                {...editableProps(field.id, {
                  pageNumber: field.page_number,
                  region: field.region,
                })}
              />
            )
          }
          const table = tablesById.get(targetId)
          if (table) {
            return (
              <ReviewTable
                editableProps={editableProps}
                key={table.id}
                onSelectionChange={
                  approved
                    ? undefined
                    : (nextSelected) =>
                        setTargetSelected(table.id, nextSelected)
                }
                selected={selected}
                selectionDisabled={selectionDisabled}
                sectionTitle={section.title}
                table={table}
              />
            )
          }
          const block = textBlocksById.get(targetId)
          if (block) {
            return (
              <EditableValue
                hideLabel
                key={block.id}
                label={`${section.title} text`}
                pageNumber={block.page_number}
                selected={selected}
                selectionDisabled={selectionDisabled}
                selectionLabel={`Include ${section.title} text in Tally JSON`}
                onSelectionChange={
                  approved
                    ? undefined
                    : (nextSelected) =>
                        setTargetSelected(block.id, nextSelected)
                }
                value={block.text}
                {...editableProps(block.id, {
                  pageNumber: block.page_number,
                  region: block.region,
                })}
              />
            )
          }
          return null
        })}
      </Card>
    )
  }
  const reviewSectionGroups = sourcePaneVisible
    ? []
    : groupReviewSections(visiblePresentationSections, tablesById)

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
            <Button onClick={toggleOriginal} variant="outline">
              {showOriginal ? <RiEyeOffLine /> : <RiEyeLine />}
              {showOriginal ? "Hide document" : "Show document"}
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
                reviewMutation.isPending ||
                correctionMutation.isPending ||
                selectionMutation.isPending ||
                selectionIsDirty
              }
              onClick={() =>
                reviewMutation.mutate({
                  expectedVersion: result.version,
                  status: "approved",
                })
              }
            >
              {reviewMutation.isPending ? "Approving…" : "Approve"}
            </Button>
          )}
        </div>
      </div>

      <div className="mt-4">
        {!approved && presentationTargetIds.length ? (
          <section className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-4 shadow-sm">
            <div>
              <h2 className="text-sm font-semibold">Tally JSON content</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                {selectedTargetCount} of {presentationTargetIds.length} items
                selected. Unchecked items stay in CAssist but are omitted from
                the export.
              </p>
              {selectionIsDirty ? (
                <p className="mt-1 text-xs font-medium text-amber-700">
                  Save this selection before approval.
                </p>
              ) : null}
              {!selectedTargetCount ? (
                <p className="mt-1 text-xs text-destructive">
                  Select at least one item for export.
                </p>
              ) : null}
            </div>
            <Button
              disabled={
                !selectionIsDirty ||
                !selectedTargetCount ||
                selectionMutation.isPending
              }
              onClick={() =>
                selectionMutation.mutate({
                  excludedTargetIds: orderedDraftExcludedTargetIds,
                  expectedVersion: result.version,
                })
              }
              variant="outline"
            >
              {selectionMutation.isPending
                ? "Saving selection…"
                : "Save Tally selection"}
            </Button>
          </section>
        ) : null}
        {unmappedIssues.length ? (
          <section
            aria-label="Document quality issues"
            className="rounded-xl border border-destructive/30 bg-destructive/5 p-4"
          >
            <h2 className="text-sm font-semibold text-destructive">
              Document-level issues
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              These checks apply to the document rather than one extracted
              value.
            </p>
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-destructive">
              {unmappedIssues.map((issue) => (
                <li key={`${issue.target_id}-${issue.code}`}>
                  {issue.message}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        {mutationError ? (
          <p className="mt-3 text-sm text-destructive" role="alert">
            {mutationError.message}
          </p>
        ) : null}
      </div>

      <div
        className={`mt-6 grid min-w-0 grid-cols-[minmax(0,1fr)] items-start gap-5 ${sourcePaneVisible ? "lg:grid-cols-2" : ""}`}
      >
        {sourcePaneVisible ? (
          <SourcePreview
            activeEvidence={activeEvidence}
            error={sourceMutation.error}
            loading={sourceMutation.isPending}
            mimeType={result.original_mime_type}
            onRetry={() => {
              sourceMutation.reset()
              sourceMutation.mutate(result.document_id)
            }}
            sourceUrl={sourceMutation.data?.url}
          />
        ) : null}
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-5">
          {sourcePaneVisible
            ? visiblePresentationSections.map((section) =>
                renderSectionCard(section)
              )
            : reviewSectionGroups.map((group) => {
                if (group.type === "wide") {
                  return (
                    <div
                      className="min-w-0"
                      data-review-layout="wide"
                      key={group.id}
                    >
                      {renderSectionCard(group.section)}
                    </div>
                  )
                }
                if (group.sections.length === 1) {
                  return (
                    <div
                      className="min-w-0"
                      data-review-layout="wide"
                      key={group.id}
                    >
                      {renderSectionCard(group.sections[0])}
                    </div>
                  )
                }
                return (
                  <div
                    className="min-w-0 columns-1 gap-5 lg:columns-2"
                    data-review-layout="balanced"
                    key={group.id}
                  >
                    {group.sections.map((section) =>
                      renderSectionCard(
                        section,
                        "mb-5 break-inside-avoid-column"
                      )
                    )}
                  </div>
                )
              })}
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
          {result.corrections.length ? (
            <details className="rounded-xl border bg-card px-4 py-3 text-sm lg:col-span-2">
              <summary className="cursor-pointer font-medium">
                Changes ({result.corrections.length})
              </summary>
              <CorrectionHistory
                corrections={result.corrections}
                targetLabels={targetLabels}
              />
            </details>
          ) : null}
        </div>
      </div>
    </section>
  )
}
