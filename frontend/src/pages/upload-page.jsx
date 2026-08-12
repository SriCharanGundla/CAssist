import * as React from "react"
import { RiArrowLeftSLine } from "@remixicon/react"
import { Link, useNavigate } from "react-router-dom"

import { Button } from "@/components/ui/button"
import {
  ACCEPTED_UPLOAD_TYPES,
  MAX_UPLOAD_BYTES,
  uploadDocument,
} from "@/lib/api"

const STAGE_LABELS = {
  creating: "Creating a secure upload…",
  uploading: "Uploading to private storage…",
  verifying: "Verifying the file and queuing extraction…",
}

function validateFile(file) {
  if (!ACCEPTED_UPLOAD_TYPES.includes(file.type)) {
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
  const inputRef = React.useRef(null)
  const [file, setFile] = React.useState(null)
  const [error, setError] = React.useState(null)
  const [stage, setStage] = React.useState(null)

  const selectFile = (selectedFile) => {
    if (!selectedFile) {
      return
    }
    const validationError = validateFile(selectedFile)
    setError(validationError)
    setFile(validationError ? null : selectedFile)
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    if (!file || stage) {
      return
    }
    setError(null)
    try {
      const completed = await uploadDocument(file, { onStage: setStage })
      navigate(`/documents/${completed.document_id}`, {
        replace: true,
        state: { deduplicated: completed.deduplicated },
      })
    } catch (uploadError) {
      setError(uploadError.message)
      setStage(null)
    }
  }

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
        Upload one invoice
      </h1>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">
        PDF, JPEG, or PNG, up to 25 MiB. The original stays private and is not
        treated as accounting-ready until you review the extraction.
      </p>

      <form className="mt-7 space-y-5" onSubmit={handleSubmit}>
        <div
          className="rounded-2xl border border-dashed bg-card p-8 text-center shadow-sm"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault()
            if (!stage) selectFile(event.dataTransfer.files[0])
          }}
        >
          <input
            accept={ACCEPTED_UPLOAD_TYPES.join(",")}
            className="sr-only"
            disabled={Boolean(stage)}
            onChange={(event) => selectFile(event.target.files[0])}
            ref={inputRef}
            type="file"
          />
          {file ? (
            <div>
              <p className="text-sm font-medium break-all">{file.name}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {formatBytes(file.size)}
              </p>
              {!stage ? (
                <Button
                  className="mt-4"
                  onClick={() => inputRef.current?.click()}
                  type="button"
                  variant="outline"
                >
                  Choose another file
                </Button>
              ) : null}
            </div>
          ) : (
            <div>
              <p className="text-sm font-medium">Drop an invoice here</p>
              <p className="mt-1 text-xs text-muted-foreground">or</p>
              <Button
                className="mt-3"
                onClick={() => inputRef.current?.click()}
                type="button"
                variant="outline"
              >
                Choose a file
              </Button>
            </div>
          )}
        </div>

        {stage ? (
          <div aria-live="polite" className="rounded-lg bg-muted p-4 text-sm">
            <p className="font-medium">{STAGE_LABELS[stage]}</p>
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

        <Button disabled={!file || Boolean(stage)} size="lg" type="submit">
          {stage ? "Uploading…" : "Upload and process"}
        </Button>
      </form>
    </section>
  )
}
