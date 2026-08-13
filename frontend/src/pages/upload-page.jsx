import * as React from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { RiArrowLeftSLine, RiCloseLine } from "@remixicon/react"
import { Link, useNavigate } from "react-router-dom"

import { Button } from "@/components/ui/button"
import {
  getStorageQuota,
  getUploadCapabilities,
  uploadMimeType,
  uploadDocument,
} from "@/lib/api"

const STAGE_LABELS = {
  creating: "Creating a secure upload…",
  uploading: "Uploading to private storage…",
  verifying: "Verifying the file and queuing extraction…",
  complete: "Queued for extraction",
  cancelled: "Upload cancelled",
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

function validateFile(file, capabilities) {
  if (!capabilities.accepted_mime_types.includes(uploadMimeType(file))) {
    return "Choose a PDF, JPEG, or PNG file."
  }
  if (file.size > capabilities.maximum_file_bytes) {
    return `Choose a file smaller than ${formatBytes(capabilities.maximum_file_bytes)}.`
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
  const uploadControllers = React.useRef(new Map())
  const [items, setItems] = React.useState([])
  const [error, setError] = React.useState(null)
  const [dragActive, setDragActive] = React.useState(false)
  const [uploading, setUploading] = React.useState(false)
  const quotaQuery = useQuery({
    queryKey: ["storage-quota"],
    queryFn: ({ signal }) => getStorageQuota({ signal }),
    staleTime: 5_000,
  })
  const capabilitiesQuery = useQuery({
    queryKey: ["upload-capabilities"],
    queryFn: ({ signal }) => getUploadCapabilities({ signal }),
    staleTime: 60 * 60 * 1_000,
  })
  const quota = quotaQuery.data
  const capabilities = capabilitiesQuery.data

  React.useEffect(() => {
    if (!uploading) return undefined
    const warnBeforeLeaving = (event) => {
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", warnBeforeLeaving)
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving)
  }, [uploading])

  React.useEffect(
    () => () => {
      for (const controller of uploadControllers.current.values()) {
        controller.abort()
      }
    },
    []
  )

  const selectFiles = (selectedFiles) => {
    const files = Array.from(selectedFiles || [])
    if (!files.length) {
      return
    }
    if (!capabilities) return
    if (files.length > capabilities.maximum_batch_files) {
      setError(
        `Choose no more than ${capabilities.maximum_batch_files} files at once.`
      )
      setItems([])
      return
    }
    const selections = files.map((file) => ({
      file,
      validationError: validateFile(file, capabilities),
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
        progress: 0,
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
    const pendingBytes = pendingItems.reduce(
      (total, item) => total + item.file.size,
      0
    )
    if (
      !pendingItems.length ||
      uploading ||
      !quota?.upload_allowed ||
      pendingBytes > quota.available_bytes
    ) {
      return
    }
    setError(null)
    setUploading(true)
    for (const item of pendingItems) {
      uploadControllers.current.set(item.id, new AbortController())
      updateItem(item.id, { error: null, progress: 0, stage: null })
    }
    const outcomes = await mapWithConcurrency(
      pendingItems,
      MAX_CONCURRENT_UPLOADS,
      async (item) => {
        const controller = uploadControllers.current.get(item.id)
        try {
          const result = await uploadDocument(item.file, {
            onProgress: (progress) => updateItem(item.id, { progress }),
            onStage: (stage) => updateItem(item.id, { stage }),
            signal: controller.signal,
          })
          updateItem(item.id, { progress: 100, result, stage: "complete" })
          return { result }
        } catch (uploadError) {
          if (uploadError.name === "AbortError") {
            updateItem(item.id, {
              error: null,
              progress: 0,
              stage: "cancelled",
            })
            return { cancelled: true }
          }
          updateItem(item.id, { error: uploadError.message, stage: null })
          return { error: uploadError }
        } finally {
          uploadControllers.current.delete(item.id)
        }
      }
    )
    setUploading(false)
    await queryClient.invalidateQueries({ queryKey: ["storage-quota"] })
    const failedCount = outcomes.filter((outcome) => outcome.error).length
    const cancelledCount = outcomes.filter(
      (outcome) => outcome.cancelled
    ).length
    if (!failedCount && !cancelledCount) {
      const completedResults = [
        ...items.flatMap((item) => (item.result ? [item.result] : [])),
        ...outcomes.flatMap((outcome) =>
          outcome.result ? [outcome.result] : []
        ),
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
    } else if (failedCount) {
      setError(
        `${failedCount} of ${outcomes.length} files failed. Retry the failed files.`
      )
    } else {
      setError(
        `${cancelledCount} ${cancelledCount === 1 ? "upload was" : "uploads were"} cancelled. Retry when ready.`
      )
    }
  }

  const cancelUpload = (id) => uploadControllers.current.get(id)?.abort()
  const cancelAllUploads = () => {
    for (const controller of uploadControllers.current.values()) {
      controller.abort()
    }
  }

  const pendingCount = items.filter(
    (item) => !item.result && !item.validationError
  ).length
  const completedCount = items.filter((item) => item.result).length
  const pendingBytes = items
    .filter((item) => !item.result && !item.validationError)
    .reduce((total, item) => total + item.file.size, 0)
  const quotaUnavailable = quotaQuery.isPending || Boolean(quotaQuery.error)
  const capabilitiesUnavailable =
    capabilitiesQuery.isPending || Boolean(capabilitiesQuery.error)
  const storageFull = Boolean(quota && !quota.upload_allowed)
  const selectionExceedsQuota = Boolean(
    quota && pendingBytes > quota.available_bytes
  )
  const selectionDisabled = uploading || storageFull || capabilitiesUnavailable

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
        Up to {capabilities?.maximum_batch_files ?? "…"} PDF, JPEG, or PNG
        files,{" "}
        {capabilities ? formatBytes(capabilities.maximum_file_bytes) : "…"}{" "}
        each. Originals stay private and require review after extraction.
      </p>
      {quota ? (
        <p className="mt-2 text-xs text-muted-foreground">
          {(quota.available_bytes / 1_000_000_000).toFixed(2)} GB available in
          shared document storage.
        </p>
      ) : null}

      <form className="mt-7 space-y-5" onSubmit={handleSubmit}>
        <div
          aria-disabled={selectionDisabled}
          aria-label="Document drop zone"
          className={`rounded-2xl border border-dashed p-8 text-center shadow-sm transition-colors ${dragActive ? "border-primary bg-primary/5" : "bg-card"}`}
          data-drag-active={dragActive || undefined}
          onDragEnter={(event) => {
            event.preventDefault()
            if (selectionDisabled) return
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
            if (!selectionDisabled) selectFiles(event.dataTransfer.files)
          }}
        >
          <input
            accept={
              capabilities
                ? [
                    ...capabilities.accepted_mime_types,
                    ...capabilities.accepted_extensions,
                  ].join(",")
                : undefined
            }
            className="sr-only"
            disabled={selectionDisabled}
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
                      {item.stage === "uploading" ? (
                        <div
                          aria-label={`${item.file.name} upload progress`}
                          aria-valuemax="100"
                          aria-valuemin="0"
                          aria-valuenow={item.progress}
                          className="mt-2 h-1.5 overflow-hidden rounded-full bg-background"
                          role="progressbar"
                        >
                          <div
                            className="h-full rounded-full bg-primary transition-[width]"
                            style={{ width: `${item.progress}%` }}
                          />
                        </div>
                      ) : null}
                    </div>
                    {uploading && !item.result && !item.validationError ? (
                      <Button
                        aria-label={`Cancel upload ${item.file.name}`}
                        onClick={() => cancelUpload(item.id)}
                        size="icon"
                        type="button"
                        variant="destructive"
                      >
                        <RiCloseLine />
                      </Button>
                    ) : !uploading && !item.result ? (
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
                  disabled={selectionDisabled}
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
                Drop up to {capabilities?.maximum_batch_files ?? "…"} documents
                here
              </p>
              <p className="mt-1 text-xs text-muted-foreground">or</p>
              <Button
                className="mt-3"
                disabled={selectionDisabled}
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
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="font-medium">Uploading selected files…</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Keep this page open until verification finishes.
                </p>
              </div>
              <Button
                onClick={cancelAllUploads}
                type="button"
                variant="outline"
              >
                Cancel all
              </Button>
            </div>
          </div>
        ) : null}
        {error ? (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}
        {quotaQuery.error ? (
          <p className="text-sm text-destructive" role="alert">
            {quotaQuery.error.message}
          </p>
        ) : capabilitiesQuery.error ? (
          <p className="text-sm text-destructive" role="alert">
            {capabilitiesQuery.error.message}
          </p>
        ) : storageFull ? (
          <p className="text-sm text-destructive" role="alert">
            Shared document storage is full. Delete stored files before
            uploading more.
          </p>
        ) : selectionExceedsQuota ? (
          <p className="text-sm text-destructive" role="alert">
            These files exceed the remaining shared storage. Remove files or
            delete stored documents first.
          </p>
        ) : null}

        <Button
          disabled={
            !pendingCount ||
            uploading ||
            quotaUnavailable ||
            capabilitiesUnavailable ||
            storageFull ||
            selectionExceedsQuota
          }
          size="lg"
          type="submit"
        >
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
