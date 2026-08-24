import * as React from "react"
import {
  RiArrowDownSLine,
  RiArrowRightSLine,
  RiCheckboxCircleLine,
} from "@remixicon/react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

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
        className="cursor-pointer text-left text-sm font-medium break-words whitespace-pre-wrap transition-colors hover:text-muted-foreground"
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

function QualityIssues({ canEdit, issues, onUseSuggestion, saving }) {
  if (!issues.length) return null
  return (
    <ul className="mt-2 space-y-2">
      {issues.map((issue) => (
        <li
          className="rounded-lg bg-destructive/10 px-3 py-2 text-xs"
          key={issue.code}
        >
          <p className="text-destructive">{issue.message}</p>
          {canEdit && issue.suggested_value !== null ? (
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

export function EditableValue({
  canEdit = true,
  edited = false,
  issues,
  hideLabel = false,
  label,
  onEvidenceChange,
  onNavigate,
  onSave,
  originalValue,
  pageNumber,
  saving,
  selected = true,
  selectionDisabled = false,
  selectionLabel,
  onSelectionChange,
  targetId,
  value,
}) {
  const [editing, setEditing] = React.useState(false)
  const [draft, setDraft] = React.useState(value)
  const [reason, setReason] = React.useState("")
  const editorRef = React.useRef(null)

  React.useLayoutEffect(() => {
    const editor = editorRef.current
    if (!editing || !editor) return
    editor.style.height = "auto"
    const nextHeight = Math.min(320, Math.max(40, editor.scrollHeight))
    editor.style.height = `${nextHeight}px`
    editor.style.overflowY = editor.scrollHeight > 320 ? "auto" : "hidden"
  }, [draft, editing])

  const save = async (nextValue = draft, nextReason = reason) => {
    try {
      await onSave(targetId, nextValue, nextReason)
      setEditing(false)
      setReason("")
      return true
    } catch {
      // The page-level mutation error remains visible while this editor stays open.
      return false
    }
  }
  const cancelEditing = () => {
    setDraft(value)
    setReason("")
    setEditing(false)
  }
  const handleEditorKeyDown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault()
      cancelEditing()
    } else if (
      event.key === "Enter" &&
      (event.metaKey || event.ctrlKey) &&
      draft &&
      !saving
    ) {
      event.preventDefault()
      save()
    } else if (event.key === "Tab" && onNavigate) {
      event.preventDefault()
      const direction = event.shiftKey ? -1 : 1
      const move = async () => {
        if (draft !== value || reason.trim()) {
          const saved = await save()
          if (!saved) return
        } else cancelEditing()
        onNavigate(targetId, direction)
      }
      move()
    }
  }

  return (
    <div
      className={`border-b py-4 transition-opacity last:border-b-0 ${selected ? "" : "opacity-55"}`}
      data-review-target={targetId}
      onBlur={() => onEvidenceChange?.(null)}
      onFocus={() => onEvidenceChange?.()}
      onMouseEnter={() => onEvidenceChange?.()}
      onMouseLeave={() => onEvidenceChange?.(null)}
    >
      <div className="flex items-start justify-between gap-4">
        {onSelectionChange ? (
          <Checkbox
            aria-label={selectionLabel || `Include ${label} in Tally JSON`}
            checked={selected}
            className="mt-1"
            disabled={selectionDisabled}
            onCheckedChange={onSelectionChange}
          />
        ) : null}
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
            {edited ? (
              <span className="rounded-full bg-secondary px-2 py-0.5 text-[11px] text-secondary-foreground">
                Edited
              </span>
            ) : null}
          </div>
          {canEdit && editing ? (
            <>
              <textarea
                aria-label={label}
                className="mt-2 min-h-10 w-full rounded-md border bg-background px-3 py-2 text-sm"
                disabled={saving}
                maxLength={20000}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={handleEditorKeyDown}
                ref={editorRef}
                rows={1}
                value={draft}
              />
              <input
                aria-label={`Reason for changing ${label}`}
                className="mt-2 h-9 w-full rounded-md border bg-background px-3 text-sm"
                disabled={saving}
                maxLength={1000}
                onChange={(event) => setReason(event.target.value)}
                onKeyDown={handleEditorKeyDown}
                placeholder="Reason for correction (optional)"
                value={reason}
              />
            </>
          ) : (
            <div className="mt-1">
              <CopyValue label={label} value={value} />
              {edited &&
              originalValue !== undefined &&
              originalValue !== value ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  <span className="font-medium">Original:</span>{" "}
                  <span className="break-words">{originalValue}</span>
                </p>
              ) : null}
            </div>
          )}
          <QualityIssues
            canEdit={canEdit}
            issues={issues}
            onUseSuggestion={(suggestion) =>
              save(suggestion, "Accepted quality-review suggestion")
            }
            saving={saving}
          />
        </div>
        {canEdit && editing ? (
          <div className="flex gap-2">
            <Button disabled={saving || !draft} onClick={() => save()}>
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button disabled={saving} onClick={cancelEditing} variant="ghost">
              Cancel
            </Button>
          </div>
        ) : canEdit ? (
          <Button
            data-edit-target={targetId}
            onClick={() => {
              setDraft(value)
              setEditing(true)
            }}
            variant="outline"
          >
            Edit
          </Button>
        ) : null}
      </div>
    </div>
  )
}

export function Card({
  children,
  className = "",
  collapsible = false,
  description,
  headerControl,
  itemCount,
  itemSummary,
  title,
}) {
  const [open, setOpen] = React.useState(true)
  return (
    <section
      className={`min-w-0 overflow-hidden rounded-2xl border bg-card p-5 shadow-sm ${className}`}
    >
      {collapsible ? (
        <div className="flex items-center gap-3">
          {headerControl}
          <button
            aria-expanded={open}
            aria-label={`${open ? "Collapse" : "Expand"} ${title} section`}
            className="flex min-w-0 flex-1 flex-col items-start gap-1 text-left sm:flex-row sm:items-center sm:justify-between sm:gap-3"
            onClick={() => setOpen((current) => !current)}
            type="button"
          >
            <span className="min-w-0 text-base leading-6 font-semibold break-words">
              {title}
            </span>
            <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
              {itemSummary ||
                `${itemCount} ${itemCount === 1 ? "item" : "items"}`}
              {open ? <RiArrowDownSLine /> : <RiArrowRightSLine />}
            </span>
          </button>
        </div>
      ) : (
        <h2 className="font-semibold">{title}</h2>
      )}
      {description ? (
        <p className="mt-1 text-xs text-muted-foreground">{description}</p>
      ) : null}
      {!collapsible || open ? (
        <div
          aria-label={`${title} content`}
          className="mt-3 max-h-[36rem] min-w-0 overflow-y-auto overscroll-contain pr-2"
          role="region"
          tabIndex={0}
        >
          {children}
        </div>
      ) : null}
    </section>
  )
}

export function SectionSelectionCheckbox({
  disabled,
  onChange,
  selectedCount,
  targetCount,
  title,
}) {
  const partiallySelected = selectedCount > 0 && selectedCount < targetCount
  return (
    <Checkbox
      aria-label={`Include ${title} section in Tally JSON`}
      checked={selectedCount === targetCount}
      disabled={disabled}
      indeterminate={partiallySelected}
      onCheckedChange={onChange}
    />
  )
}

export function CorrectionHistory({ corrections, targetLabels }) {
  if (!corrections.length)
    return (
      <p className="mt-3 text-sm text-muted-foreground">No corrections yet.</p>
    )
  return (
    <ol className="mt-3 space-y-3">
      {corrections.map((correction) => (
        <li className="rounded-lg border p-3 text-xs" key={correction.id}>
          <p className="font-medium">
            {targetLabels.get(correction.target_id) || "Extracted value"}
          </p>
          <dl className="mt-2 grid gap-2 sm:grid-cols-2">
            <div className="rounded-md bg-muted p-2">
              <dt className="font-medium text-muted-foreground">Before</dt>
              <dd className="mt-0.5 break-words whitespace-pre-wrap">
                {String(correction.previous_value)}
              </dd>
            </div>
            <div className="rounded-md bg-secondary p-2">
              <dt className="font-medium text-muted-foreground">After</dt>
              <dd className="mt-0.5 font-medium break-words whitespace-pre-wrap">
                {String(correction.corrected_value)}
              </dd>
            </div>
          </dl>
          {correction.reason ? (
            <p className="mt-1 text-muted-foreground">{correction.reason}</p>
          ) : null}
        </li>
      ))}
    </ol>
  )
}

export function ReviewTable({
  editableProps,
  onSelectionChange,
  sectionTitle,
  selected,
  selectionDisabled,
  table,
}) {
  return (
    <div
      className={`border-t pt-4 transition-opacity first:border-t-0 first:pt-0 ${selected ? "" : "opacity-55"}`}
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          {onSelectionChange ? (
            <Checkbox
              aria-label={`Include ${table.title || sectionTitle} table in Tally JSON`}
              checked={selected}
              disabled={selectionDisabled}
              onCheckedChange={onSelectionChange}
            />
          ) : null}
          {table.title && table.title !== sectionTitle ? (
            <h3 className="text-sm font-medium">{table.title}</h3>
          ) : (
            <span className="text-sm font-medium">Table</span>
          )}
        </div>
        <span className="text-[11px] text-muted-foreground">
          Page{table.page_numbers.length === 1 ? "" : "s"}{" "}
          {table.page_numbers.join(", ")}
        </span>
      </div>
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
            {table.rows.map((row, rowIndex) => (
              <tr className="border-b last:border-b-0" key={row.id}>
                {row.cells.map((cell, cellIndex) => (
                  <td className="min-w-36 px-2 align-top" key={cell.id}>
                    <EditableValue
                      hideLabel
                      label={`${table.headers[cellIndex]}, row ${rowIndex + 1}`}
                      value={cell.value}
                      {...editableProps(cell.id, {
                        pageNumber: table.page_numbers[0],
                        region: null,
                      })}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
