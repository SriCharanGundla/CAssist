import * as React from "react"
import {
  RiArrowDownSLine,
  RiArrowUpSLine,
  RiAspectRatioLine,
  RiClockwise2Line,
  RiDragMove2Line,
  RiExternalLinkLine,
  RiRestartLine,
  RiZoomInLine,
  RiZoomOutLine,
} from "@remixicon/react"
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url"

import { Button } from "@/components/ui/button"

const MIN_ZOOM = 0.5
const MAX_ZOOM = 3
const ZOOM_STEP = 0.25
const MIN_VISIBLE_IMAGE_PIXELS = 48
const SHARP_RENDER_DELAY_MS = 180
const SPREADSHEET_MIME_TYPES = new Set([
  "text/csv",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
])

function SpreadsheetPreview({ sourceUrl }) {
  return (
    <div className="grid flex-1 place-items-center p-6 text-center">
      <div>
        <p className="text-sm text-muted-foreground">
          Open the original spreadsheet to compare it with the extraction.
        </p>
        <Button
          className="mt-4"
          nativeButton={false}
          render={
            <a href={sourceUrl} rel="noreferrer" target="_blank">
              Open original <RiExternalLinkLine />
            </a>
          }
          variant="outline"
        />
      </div>
    </div>
  )
}

function ImagePreview({ sourceUrl }) {
  const [zoom, setZoom] = React.useState(1)
  const [position, setPosition] = React.useState({ x: 0, y: 0 })
  const drag = React.useRef(null)
  const imageRef = React.useRef(null)
  const viewportRef = React.useRef(null)

  const clampPosition = React.useCallback((nextPosition, nextZoom = zoom) => {
    const image = imageRef.current
    const viewport = viewportRef.current
    if (!image || !viewport || nextZoom <= 1) return { x: 0, y: 0 }
    const maximumX = Math.max(
      0,
      (viewport.clientWidth + image.offsetWidth * nextZoom) / 2 -
        MIN_VISIBLE_IMAGE_PIXELS
    )
    const maximumY = Math.max(
      0,
      (viewport.clientHeight + image.offsetHeight * nextZoom) / 2 -
        MIN_VISIBLE_IMAGE_PIXELS
    )
    return {
      x: Math.min(maximumX, Math.max(-maximumX, nextPosition.x)),
      y: Math.min(maximumY, Math.max(-maximumY, nextPosition.y)),
    }
  }, [zoom])

  const reset = () => {
    setZoom(1)
    setPosition({ x: 0, y: 0 })
  }

  const changeZoom = (nextZoom) => {
    const bounded = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom))
    setZoom(bounded)
    setPosition((current) => clampPosition(current, bounded))
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
        aria-label="Image document viewer"
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
          setPosition(
            clampPosition({
              x: drag.current.startX + event.clientX - drag.current.pointerX,
              y: drag.current.startY + event.clientY - drag.current.pointerY,
            })
          )
        }}
        onPointerUp={() => {
          drag.current = null
        }}
        ref={viewportRef}
      >
        <img
          alt="Original document"
          className="absolute inset-0 m-auto max-h-full max-w-full object-contain transition-transform duration-100 select-none"
          draggable="false"
          ref={imageRef}
          src={sourceUrl}
          style={{
            transform: `translate(${position.x}px, ${position.y}px) scale(${zoom})`,
          }}
        />
      </div>
    </div>
  )
}

function PdfPage({
  descriptor,
  registerPage,
  renderView,
  rotation,
  scrollRoot,
  zoom,
}) {
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
    const renderViewport = descriptor.page.getViewport({
      rotation: renderView.rotation,
      scale: renderView.zoom * outputScale,
    })
    const canvas = canvasRef.current
    canvas.width = Math.ceil(renderViewport.width)
    canvas.height = Math.ceil(renderViewport.height)
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
  }, [descriptor.page, nearViewport, renderView])

  const displayViewport = descriptor.page.getViewport({
    rotation,
    scale: zoom,
  })

  return (
    <div
      aria-label={`Page ${descriptor.pageNumber}`}
      className="relative shrink-0 overflow-hidden bg-white shadow-sm ring-1 ring-black/10"
      data-page-number={descriptor.pageNumber}
      ref={(element) => {
        wrapperRef.current = element
        registerPage(descriptor.pageNumber, element)
      }}
      style={{
        contain: "layout paint",
        height: displayViewport.height,
        width: displayViewport.width,
      }}
    >
      {nearViewport ? (
        <>
          <canvas
            aria-hidden="true"
            className={`block size-full transition-opacity duration-150 ${rendering ? "opacity-70" : "opacity-100"}`}
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
  const [rotation, setRotation] = React.useState(0)
  const [renderView, setRenderView] = React.useState({ rotation: 0, zoom: 1 })
  const [currentPage, setCurrentPage] = React.useState(1)
  const [scrollRoot, setScrollRoot] = React.useState(null)
  const drag = React.useRef(null)
  const pageElements = React.useRef(new Map())

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
    const timeout = window.setTimeout(() => {
      setRenderView((current) =>
        current.rotation === rotation && current.zoom === zoom
          ? current
          : { rotation, zoom }
      )
    }, SHARP_RENDER_DELAY_MS)
    return () => window.clearTimeout(timeout)
  }, [rotation, zoom])

  React.useEffect(() => {
    if (!scrollRoot || !pages.length) return undefined

    const visibility = new Map()
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          visibility.set(
            Number(entry.target.dataset.pageNumber),
            entry.intersectionRatio
          )
        }
        let visiblePage = 1
        let visibleRatio = 0
        for (const [pageNumber, ratio] of visibility) {
          if (ratio > visibleRatio) {
            visiblePage = pageNumber
            visibleRatio = ratio
          }
        }
        if (visibleRatio > 0) setCurrentPage(visiblePage)
      },
      { root: scrollRoot, threshold: [0.25, 0.5, 0.75] }
    )
    for (const element of pageElements.current.values()) observer.observe(element)
    return () => observer.disconnect()
  }, [pages, scrollRoot])

  const changeZoom = (nextZoom) => {
    setZoom(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom)))
  }

  const registerPage = React.useCallback((pageNumber, element) => {
    if (element) pageElements.current.set(pageNumber, element)
    else pageElements.current.delete(pageNumber)
  }, [])

  const goToPage = (pageNumber, behavior = "smooth") => {
    const boundedPage = Math.min(pages.length, Math.max(1, pageNumber))
    const pageElement = pageElements.current.get(boundedPage)
    setCurrentPage(boundedPage)
    if (!scrollRoot || !pageElement) return
    const top = Math.max(0, pageElement.offsetTop - 16)
    scrollRoot.scrollTo({ behavior, top })
  }

  const fitCurrentPage = () => {
    const descriptor = pages[currentPage - 1]
    if (!descriptor || !scrollRoot) return
    const viewport = descriptor.page.getViewport({ rotation, scale: 1 })
    const availableWidth = Math.max(1, scrollRoot.clientWidth - 32)
    const availableHeight = Math.max(1, scrollRoot.clientHeight - 32)
    const fittedZoom = Math.min(
      availableWidth / viewport.width,
      availableHeight / viewport.height
    )
    changeZoom(fittedZoom)
    window.requestAnimationFrame(() => goToPage(currentPage, "auto"))
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
          onClick={() => {
            setZoom(1)
            setRotation(0)
            goToPage(1)
          }}
          size="icon"
          variant="ghost"
        >
          <RiRestartLine />
        </Button>
        <Button
          aria-label="Fit page"
          disabled={!pages.length}
          onClick={fitCurrentPage}
          size="icon"
          variant="ghost"
        >
          <RiAspectRatioLine />
        </Button>
        <Button
          aria-label="Rotate clockwise"
          disabled={!pages.length}
          onClick={() => setRotation((current) => (current + 90) % 360)}
          size="icon"
          variant="ghost"
        >
          <RiClockwise2Line />
        </Button>
        <div className="ml-1 flex items-center gap-0.5 border-l pl-2">
          <Button
            aria-label="Previous page"
            disabled={!pages.length || currentPage <= 1}
            onClick={() => goToPage(currentPage - 1)}
            size="icon"
            variant="ghost"
          >
            <RiArrowUpSLine />
          </Button>
          <span className="min-w-12 text-center text-xs text-muted-foreground">
            {pages.length ? `${currentPage} / ${pages.length}` : "…"}
          </span>
          <Button
            aria-label="Next page"
            disabled={!pages.length || currentPage >= pages.length}
            onClick={() => goToPage(currentPage + 1)}
            size="icon"
            variant="ghost"
          >
            <RiArrowDownSLine />
          </Button>
        </div>
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
            event.preventDefault()
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
            event.preventDefault()
            drag.current.scrollElement.scrollLeft =
              drag.current.left + drag.current.pointerX - event.clientX
            drag.current.scrollElement.scrollTop =
              drag.current.top + drag.current.pointerY - event.clientY
          }}
          onPointerUp={stopDragging}
          onKeyDown={(event) => {
            if (event.key === "+" || event.key === "=") {
              event.preventDefault()
              changeZoom(zoom + ZOOM_STEP)
            } else if (event.key === "-") {
              event.preventDefault()
              changeZoom(zoom - ZOOM_STEP)
            }
          }}
          ref={setScrollRoot}
          style={{
            overscrollBehavior: "contain",
            scrollBehavior: "auto",
            touchAction: "none",
          }}
          tabIndex={0}
        >
          <div className="flex min-w-max flex-col items-center gap-4">
            {pages.map((descriptor) => (
              <PdfPage
                descriptor={descriptor}
                key={descriptor.pageNumber}
                registerPage={registerPage}
                renderView={renderView}
                rotation={rotation}
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
      ) : sourceUrl && SPREADSHEET_MIME_TYPES.has(mimeType) ? (
        <SpreadsheetPreview sourceUrl={sourceUrl} />
      ) : sourceUrl ? (
        <ImagePreview sourceUrl={sourceUrl} />
      ) : null}
    </section>
  )
}
