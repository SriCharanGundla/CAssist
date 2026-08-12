import * as React from "react"
import {
  RiDragMove2Line,
  RiRestartLine,
  RiZoomInLine,
  RiZoomOutLine,
} from "@remixicon/react"
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url"

import { Button } from "@/components/ui/button"

const MIN_ZOOM = 0.5
const MAX_ZOOM = 3
const ZOOM_STEP = 0.25

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

function PdfPage({ descriptor, scrollRoot, zoom }) {
  const canvasRef = React.useRef(null)
  const wrapperRef = React.useRef(null)
  const [nearViewport, setNearViewport] = React.useState(false)
  const [renderError, setRenderError] = React.useState(false)
  const [rendering, setRendering] = React.useState(false)

  React.useEffect(() => {
    const element = wrapperRef.current
    if (!element || !scrollRoot) return undefined

    const observer = new IntersectionObserver(
      ([entry]) => setNearViewport(entry.isIntersecting),
      { root: scrollRoot, rootMargin: "800px 0px" }
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [scrollRoot])

  React.useEffect(() => {
    if (!nearViewport || !canvasRef.current) return undefined

    let active = true
    const outputScale = Math.min(window.devicePixelRatio || 1, 2)
    const displayViewport = descriptor.page.getViewport({ scale: zoom })
    const renderViewport = descriptor.page.getViewport({
      scale: zoom * outputScale,
    })
    const canvas = canvasRef.current
    canvas.width = Math.ceil(renderViewport.width)
    canvas.height = Math.ceil(renderViewport.height)
    canvas.style.width = `${displayViewport.width}px`
    canvas.style.height = `${displayViewport.height}px`
    setRenderError(false)
    setRendering(true)

    const renderTask = descriptor.page.render({
      canvas,
      viewport: renderViewport,
    })
    renderTask.promise
      .catch((error) => {
        if (active && error?.name !== "RenderingCancelledException") {
          setRenderError(true)
        }
      })
      .finally(() => {
        if (active) setRendering(false)
      })

    return () => {
      active = false
      renderTask.cancel()
    }
  }, [descriptor.page, nearViewport, zoom])

  const width = descriptor.width * zoom
  const height = descriptor.height * zoom

  return (
    <div
      aria-label={`Page ${descriptor.pageNumber}`}
      className="relative shrink-0 overflow-hidden bg-white shadow-sm ring-1 ring-black/10"
      ref={wrapperRef}
      style={{ height, width }}
    >
      {nearViewport ? (
        <>
          <canvas
            aria-hidden="true"
            className={`block transition-opacity duration-150 ${rendering ? "opacity-70" : "opacity-100"}`}
            ref={canvasRef}
          />
          {renderError ? (
            <span className="absolute inset-0 grid place-items-center text-xs text-destructive">
              Could not render page {descriptor.pageNumber}.
            </span>
          ) : null}
        </>
      ) : null}
      <span className="absolute right-2 bottom-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white">
        {descriptor.pageNumber}
      </span>
    </div>
  )
}

function PdfPreview({ sourceUrl }) {
  const [pages, setPages] = React.useState([])
  const [error, setError] = React.useState(null)
  const [zoom, setZoom] = React.useState(1)
  const [scrollRoot, setScrollRoot] = React.useState(null)
  const drag = React.useRef(null)
  const frame = React.useRef(null)

  React.useEffect(() => {
    let active = true
    let loadingTask

    const load = async () => {
      try {
        const { GlobalWorkerOptions, getDocument } = await import("pdfjs-dist")
        if (!active) return
        GlobalWorkerOptions.workerSrc = pdfWorkerUrl
        loadingTask = getDocument({ url: sourceUrl })
        const loadedPdf = await loadingTask.promise
        const descriptors = await Promise.all(
          Array.from({ length: loadedPdf.numPages }, async (_, index) => {
            const page = await loadedPdf.getPage(index + 1)
            const viewport = page.getViewport({ scale: 1 })
            return {
              height: viewport.height,
              page,
              pageNumber: index + 1,
              width: viewport.width,
            }
          })
        )
        if (!active) return loadedPdf.destroy()
        setPages(descriptors)
      } catch (loadError) {
        if (active) setError(loadError)
      }
    }

    load()

    return () => {
      active = false
      loadingTask?.destroy()
    }
  }, [sourceUrl])

  React.useEffect(() => {
    return () => {
      if (frame.current) window.cancelAnimationFrame(frame.current)
    }
  }, [])

  const changeZoom = (nextZoom) => {
    setZoom(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom)))
  }

  const stopDragging = (event) => {
    drag.current = null
    if (event?.currentTarget?.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-1 border-b p-2">
        <Button
          aria-label="Zoom out"
          disabled={zoom <= MIN_ZOOM}
          onClick={() => changeZoom(zoom - ZOOM_STEP)}
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
          disabled={zoom >= MAX_ZOOM}
          onClick={() => changeZoom(zoom + ZOOM_STEP)}
          size="icon"
          variant="ghost"
        >
          <RiZoomInLine />
        </Button>
        <Button
          aria-label="Reset view"
          onClick={() => setZoom(1)}
          size="icon"
          variant="ghost"
        >
          <RiRestartLine />
        </Button>
        <span className="ml-2 text-xs text-muted-foreground">
          {pages.length
            ? `${pages.length} page${pages.length === 1 ? "" : "s"}`
            : "Loading…"}
        </span>
        <span className="ml-auto hidden items-center gap-1 text-xs text-muted-foreground sm:flex">
          <RiDragMove2Line /> Drag to pan
        </span>
      </div>
      {error ? (
        <div className="grid flex-1 place-items-center p-6 text-sm text-destructive">
          Could not render this PDF.
        </div>
      ) : (
        <div
          aria-label="PDF document viewer"
          className="min-h-96 flex-1 cursor-grab overflow-auto bg-muted/50 p-4 select-none active:cursor-grabbing"
          onPointerCancel={stopDragging}
          onPointerDown={(event) => {
            if (event.button !== 0) return
            event.currentTarget.setPointerCapture(event.pointerId)
            drag.current = {
              left: event.currentTarget.scrollLeft,
              pointerX: event.clientX,
              pointerY: event.clientY,
              scrollElement: event.currentTarget,
              top: event.currentTarget.scrollTop,
            }
          }}
          onPointerMove={(event) => {
            if (!drag.current) return
            const nextLeft =
              drag.current.left + drag.current.pointerX - event.clientX
            const nextTop =
              drag.current.top + drag.current.pointerY - event.clientY
            if (frame.current) window.cancelAnimationFrame(frame.current)
            frame.current = window.requestAnimationFrame(() => {
              if (!drag.current) return
              drag.current.scrollElement.scrollLeft = nextLeft
              drag.current.scrollElement.scrollTop = nextTop
            })
          }}
          onPointerUp={stopDragging}
          ref={setScrollRoot}
          style={{ touchAction: "none" }}
        >
          <div className="flex min-w-max flex-col items-center gap-4">
            {pages.map((descriptor) => (
              <PdfPage
                descriptor={descriptor}
                key={descriptor.pageNumber}
                scrollRoot={scrollRoot}
                zoom={zoom}
              />
            ))}
          </div>
        </div>
      )}
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
        <PdfPreview sourceUrl={sourceUrl} />
      ) : sourceUrl ? (
        <ImagePreview sourceUrl={sourceUrl} />
      ) : null}
    </section>
  )
}
