import * as React from "react"
import {
  RiDragMove2Line,
  RiRestartLine,
  RiZoomInLine,
  RiZoomOutLine,
} from "@remixicon/react"

import { Button } from "@/components/ui/button"

function ImagePreview({ sourceUrl }) {
  const [zoom, setZoom] = React.useState(1)
  const [position, setPosition] = React.useState({ x: 0, y: 0 })
  const drag = React.useRef(null)

  const reset = () => {
    setZoom(1)
    setPosition({ x: 0, y: 0 })
  }

  const changeZoom = (nextZoom) => {
    const bounded = Math.min(4, Math.max(0.5, nextZoom))
    setZoom(bounded)
    if (bounded === 1) setPosition({ x: 0, y: 0 })
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-1 border-b p-2">
        <Button
          aria-label="Zoom out"
          disabled={zoom <= 0.5}
          onClick={() => changeZoom(zoom - 0.25)}
          size="icon"
          variant="ghost"
        >
          <RiZoomOutLine />
        </Button>
        <span className="min-w-12 text-center text-xs text-muted-foreground">
          {Math.round(zoom * 100)}%
        </span>
        <Button
          aria-label="Zoom in"
          disabled={zoom >= 4}
          onClick={() => changeZoom(zoom + 0.25)}
          size="icon"
          variant="ghost"
        >
          <RiZoomInLine />
        </Button>
        <Button
          aria-label="Reset view"
          onClick={reset}
          size="icon"
          variant="ghost"
        >
          <RiRestartLine />
        </Button>
        <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
          <RiDragMove2Line /> Drag to pan
        </span>
      </div>
      <div
        className={`relative min-h-96 flex-1 overflow-hidden bg-muted/50 ${zoom > 1 ? "cursor-grab active:cursor-grabbing" : ""}`}
        onPointerDown={(event) => {
          if (zoom <= 1) return
          event.currentTarget.setPointerCapture(event.pointerId)
          drag.current = {
            pointerX: event.clientX,
            pointerY: event.clientY,
            startX: position.x,
            startY: position.y,
          }
        }}
        onPointerMove={(event) => {
          if (!drag.current) return
          setPosition({
            x: drag.current.startX + event.clientX - drag.current.pointerX,
            y: drag.current.startY + event.clientY - drag.current.pointerY,
          })
        }}
        onPointerUp={() => {
          drag.current = null
        }}
      >
        <img
          alt="Original document"
          className="absolute inset-0 m-auto max-h-full max-w-full object-contain transition-transform duration-100 select-none"
          draggable="false"
          src={sourceUrl}
          style={{
            transform: `translate(${position.x}px, ${position.y}px) scale(${zoom})`,
          }}
        />
      </div>
    </div>
  )
}

export function SourcePreview({ error, loading, mimeType, sourceUrl }) {
  return (
    <section className="sticky top-5 flex h-[calc(100svh-7rem)] min-h-[32rem] flex-col overflow-hidden rounded-2xl border bg-card shadow-sm">
      <div className="border-b px-4 py-3">
        <h2 className="font-semibold">Original document</h2>
      </div>
      {loading ? (
        <div className="grid flex-1 place-items-center text-sm text-muted-foreground">
          Loading original…
        </div>
      ) : error ? (
        <div className="grid flex-1 place-items-center p-6 text-sm text-destructive">
          {error.message}
        </div>
      ) : sourceUrl && mimeType === "application/pdf" ? (
        <iframe
          className="min-h-0 flex-1 bg-white"
          src={`${sourceUrl}#toolbar=1&navpanes=0&view=FitH`}
          title="Original PDF"
        />
      ) : sourceUrl ? (
        <ImagePreview sourceUrl={sourceUrl} />
      ) : null}
    </section>
  )
}
