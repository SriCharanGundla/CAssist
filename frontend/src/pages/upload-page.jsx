import * as React from "react"
import { useQueryClient } from "@tanstack/react-query"
import { RiArrowLeftSLine, RiCloseLine } from "@remixicon/react"
import { Link, useNavigate } from "react-router-dom"

import { Button } from "@/components/ui/button"
import {
  ACCEPTED_UPLOAD_TYPES,
  ACCEPTED_UPLOAD_EXTENSIONS,
  MAX_UPLOAD_BYTES,
  MAX_UPLOAD_FILES,
  uploadMimeType,
  uploadDocument,
} from "@/lib/api"

const STAGE_LABELS = {
  creating: "Creating a secure upload…",
  uploading: "Uploading to private storage…",
  verifying: "Verifying the file and queuing extraction…",
  complete: "Queued for extraction",
}
const MAX_CONCURRENT_UPLOADS = 3

async function mapWithConcurrency(items, limit, operation) {
  const outcomes = Array(items.length)
  let nextIndex = 0
  const worker = async () => {
    while (nextIndex < items.length) {
      const currentIndex = nextIndex
      nextIndex += 1
      outcomes[currentIndex] = await operation(items[currentIndex])
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, () => worker())
  )
  return outcomes
}

function validateFile(file) {
  if (!ACCEPTED_UPLOAD_TYPES.includes(uploadMimeType(file))) {
    return "Choose a PDF, JPEG, or PNG file."
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return "Choose a file smaller than 25 MiB."
  }
  if (file.size === 0) {
    return "The selected file is empty."
  }
  return null
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} KiB`
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

export function UploadPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const inputRef = React.useRef(null)
  const dragDepth = React.useRef(0)
  const [items, setItems] = React.useState([])
  const [error, setError] = React.useState(null)
  const [dragActive, setDragActive] = React.useState(false)
  const [uploading, setUploading] = React.useState(false)

  const selectFiles = (selectedFiles) => {
    const files = Array.from(selectedFiles || [])
    if (!files.length) {
      return
    }
    if (files.length > MAX_UPLOAD_FILES) {
      setError(`Choose no more than ${MAX_UPLOAD_FILES} files at once.`)
      setItems([])
      return
    }
    const selections = files.map((file) => ({
      file,
      validationError: validateFile(file),
    }))
    const invalidCount = selections.filter(
      ({ validationError }) => validationError
    ).length
    setError(
      invalidCount
        ? `${invalidCount} invalid ${invalidCount === 1 ? "file was" : "files were"} excluded from upload.`
        : null
    )
    setItems(
      selections.map(({ file, validationError }, index) => ({
        id: `${file.name}-${file.size}-${file.lastModified}-${index}`,
        file,
        stage: null,
        result: null,
        error: null,
        validationError,
      }))
    )
  }

  const updateItem = (id, changes) => {
    setItems((current) =>
      current.map((item) => (item.id === id ? { ...item, ...changes } : item))
    )
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    const pendingItems = items.filter(
      (item) => !item.result && !item.validationError
    )
    if (!pendingItems.length || uploading) {
      return
    }
    setError(null)
    setUploading(true)
    const outcomes = await mapWithConcurrency(
      pendingItems,
      MAX_CONCURRENT_UPLOADS,
      async (item) => {
        updateItem(item.id, { error: null })
        try {
          const result = await uploadDocument(item.file, {
            onStage: (stage) => updateItem(item.id, { stage }),
          })
          updateItem(item.id, { result, stage: "complete" })
          return { result }
        } catch (uploadError) {
          updateItem(item.id, { error: uploadError.message, stage: null })
          return { error: uploadError }
        }
      }
    )
    setUploading(false)
    const failedCount = outcomes.filter((outcome) => outcome.error).length
    if (!failedCount) {
      const completedResults = [
        ...items.flatMap((item) => (item.result ? [item.result] : [])),
        ...outcomes.map((outcome) => outcome.result),
      ]
      await queryClient.invalidateQueries({ queryKey: ["documents"] })
      navigate("/", {
        replace: true,
        state: {
          deduplicated: completedResults.every((result) => result.deduplicated),
          uploadCount: completedResults.length,
          uploaded: true,
        },
      })
    } else {
      setError(
        `${failedCount} of ${outcomes.length} files failed. Retry the failed files.`
      )
    }
  }

  const pendingCount = items.filter(
    (item) => !item.result && !item.validationError
  ).length
  const completedCount = items.filter((item) => item.result).length

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
      <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
        New document
      </p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">
        Upload documents
      </h1>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">
        Up to 10 PDF, JPEG, or PNG files, 25 MiB each. Originals stay private
        and require review after extraction.
      </p>

      <form className="mt-7 space-y-5" onSubmit={handleSubmit}>
        <div
          aria-label="Document drop zone"
          className={`rounded-2xl border border-dashed p-8 text-center shadow-sm transition-colors ${dragActive ? "border-primary bg-primary/5" : "bg-card"}`}
          data-drag-active={dragActive || undefined}
          onDragEnter={(event) => {
            event.preventDefault()
            if (uploading) return
            dragDepth.current += 1
            setDragActive(true)
          }}
          onDragLeave={(event) => {
            event.preventDefault()
            dragDepth.current = Math.max(0, dragDepth.current - 1)
            if (!dragDepth.current) setDragActive(false)
          }}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault()
            dragDepth.current = 0
            setDragActive(false)
            if (!uploading) selectFiles(event.dataTransfer.files)
          }}
        >
          <input
            accept={[
              ...ACCEPTED_UPLOAD_TYPES,
              ...ACCEPTED_UPLOAD_EXTENSIONS,
            ].join(",")}
            className="sr-only"
            disabled={uploading}
            multiple
            onChange={(event) => {
              selectFiles(event.target.files)
              event.target.value = ""
            }}
            ref={inputRef}
            type="file"
          />
          {items.length ? (
            <div>
              <ul className="space-y-2 text-left">
                {items.map((item) => (
                  <li
                    className="flex items-center justify-between gap-3 rounded-lg bg-muted px-3 py-2"
                    key={item.id}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {item.file.name}
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {item.validationError ||
                          item.error ||
                          STAGE_LABELS[item.stage] ||
                          formatBytes(item.file.size)}
                      </p>
                    </div>
                    {!uploading && !item.result ? (
                      <Button
                        aria-label={`Remove ${item.file.name}`}
                        onClick={() =>
                          setItems((current) =>
                            current.filter(
                              (candidate) => candidate.id !== item.id
                            )
                          )
                        }
                        size="icon"
                        type="button"
                        variant="ghost"
                      >
                        <RiCloseLine />
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
              {!uploading ? (
                <Button
                  className="mt-4"
                  onClick={() => inputRef.current?.click()}
                  type="button"
                  variant="outline"
                >
                  Choose different files
                </Button>
              ) : null}
            </div>
          ) : (
            <div>
              <p className="text-sm font-medium">
                Drop up to 10 documents here
              </p>
              <p className="mt-1 text-xs text-muted-foreground">or</p>
              <Button
                className="mt-3"
                onClick={() => inputRef.current?.click()}
                type="button"
                variant="outline"
              >
                Choose files
              </Button>
            </div>
          )}
        </div>

        {uploading ? (
          <div aria-live="polite" className="rounded-lg bg-muted p-4 text-sm">
            <p className="font-medium">Uploading selected files…</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Keep this page open until verification finishes.
            </p>
          </div>
        ) : null}
        {error ? (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        <Button disabled={!pendingCount || uploading} size="lg" type="submit">
          {uploading
            ? "Uploading…"
            : completedCount
              ? `Retry ${pendingCount} failed ${pendingCount === 1 ? "file" : "files"}`
              : `Upload and process${pendingCount > 1 ? ` ${pendingCount} files` : ""}`}
        </Button>
      </form>
    </section>
  )
}
