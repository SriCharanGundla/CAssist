import * as React from "react"
import {
  RiArrowDownSLine,
  RiArrowUpSLine,
  RiAspectRatioLine,
  RiClockwise2Line,
  RiDragMove2Line,
  RiRestartLine,
  RiZoomInLine,
  RiZoomOutLine,
} from "@remixicon/react"
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url"
import "@/components/pdf-text-layer.css"

import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const MIN_ZOOM = 0.5
const MAX_ZOOM = 3
const ZOOM_STEP = 0.25
const MIN_VISIBLE_IMAGE_PIXELS = 48
const SHARP_RENDER_DELAY_MS = 180

function ViewerButton({ label, ...buttonProps }) {
  return (
    <Tooltip>
      <TooltipTrigger render={<Button aria-label={label} {...buttonProps} />} />
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function PreviewError({ message, onRetry }) {
  return (
    <div className="grid flex-1 place-items-center p-6 text-center">
      <div>
        <p className="text-sm text-destructive" role="alert">
          {message}
        </p>
        {onRetry ? (
          <Button className="mt-3" onClick={onRetry} variant="outline">
            Retry
          </Button>
        ) : null}
      </div>
    </div>
  )
}

function ImagePreview({ activeEvidence, sourceUrl }) {
  const [zoom, setZoom] = React.useState(1)
  const [position, setPosition] = React.useState({ x: 0, y: 0 })
  const [imageSize, setImageSize] = React.useState({ height: 0, width: 0 })
  const [viewportSize, setViewportSize] = React.useState({
    height: 0,
    width: 0,
  })
  const drag = React.useRef(null)
  const viewportRef = React.useRef(null)
  const fitScale = imageSize.width
    ? Math.min(
        1,
        viewportSize.width / imageSize.width,
        viewportSize.height / imageSize.height
      )
    : 1
  const baseWidth = imageSize.width * fitScale
  const baseHeight = imageSize.height * fitScale

  React.useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport) return undefined
    const updateSize = () =>
      setViewportSize({
        height: viewport.clientHeight,
        width: viewport.clientWidth,
      })
    updateSize()
    window.addEventListener("resize", updateSize)
    const observer = window.ResizeObserver
      ? new ResizeObserver(updateSize)
      : null
    observer?.observe(viewport)
    return () => {
      window.removeEventListener("resize", updateSize)
      observer?.disconnect()
    }
  }, [])

  const clampPosition = React.useCallback(
    (nextPosition, nextZoom = zoom) => {
      const viewport = viewportRef.current
      if (!viewport || nextZoom <= 1) return { x: 0, y: 0 }
      const maximumX = Math.max(
        0,
        (viewport.clientWidth + baseWidth * nextZoom) / 2 -
          MIN_VISIBLE_IMAGE_PIXELS
      )
      const maximumY = Math.max(
        0,
        (viewport.clientHeight + baseHeight * nextZoom) / 2 -
          MIN_VISIBLE_IMAGE_PIXELS
      )
      return {
        x: Math.min(maximumX, Math.max(-maximumX, nextPosition.x)),
        y: Math.min(maximumY, Math.max(-maximumY, nextPosition.y)),
      }
    },
    [baseHeight, baseWidth, zoom]
  )

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
        <ViewerButton
          label="Zoom out"
          disabled={zoom <= MIN_ZOOM}
          onClick={() => changeZoom(zoom - ZOOM_STEP)}
          size="icon"
          variant="ghost"
        >
          <RiZoomOutLine />
        </ViewerButton>
        <span className="min-w-12 text-center text-xs text-muted-foreground">
          {Math.round(zoom * 100)}%
        </span>
        <ViewerButton
          label="Zoom in"
          disabled={zoom >= MAX_ZOOM}
          onClick={() => changeZoom(zoom + ZOOM_STEP)}
          size="icon"
          variant="ghost"
        >
          <RiZoomInLine />
        </ViewerButton>
        <ViewerButton
          label="Reset view"
          onClick={reset}
          size="icon"
          variant="ghost"
        >
          <RiRestartLine />
        </ViewerButton>
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
        <div
          className="absolute top-1/2 left-1/2 transition-transform duration-100"
          style={{
            height: baseHeight || "100%",
            transform: `translate(-50%, -50%) translate(${position.x}px, ${position.y}px) scale(${zoom})`,
            width: baseWidth || "100%",
          }}
        >
          <img
            alt="Original document"
            className="size-full select-none"
            draggable="false"
            onLoad={(event) =>
              setImageSize({
                height: event.currentTarget.naturalHeight,
                width: event.currentTarget.naturalWidth,
              })
            }
            src={sourceUrl}
          />
          {activeEvidence?.region && imageSize.width ? (
            <span
              aria-label="Highlighted source region"
              className="pointer-events-none absolute rounded-sm border-2 border-primary bg-primary/20 shadow-sm"
              style={{
                height: `${(activeEvidence.region.height / imageSize.height) * 100}%`,
                left: `${(activeEvidence.region.x / imageSize.width) * 100}%`,
                top: `${(activeEvidence.region.y / imageSize.height) * 100}%`,
                width: `${(activeEvidence.region.width / imageSize.width) * 100}%`,
              }}
            />
          ) : null}
        </div>
      </div>
    </div>
  )
}

function PdfPage({
  activeEvidence,
  descriptor,
  registerPage,
  renderView,
  rotation,
  scrollRoot,
  zoom,
}) {
  const canvasRef = React.useRef(null)
  const textLayerRef = React.useRef(null)
  const wrapperRef = React.useRef(null)
  const [nearViewport, setNearViewport] = React.useState(false)
  const [renderError, setRenderError] = React.useState(false)
  const [renderAttempt, setRenderAttempt] = React.useState(0)
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
  }, [descriptor.page, nearViewport, renderAttempt, renderView])

  const displayViewport = React.useMemo(
    () => descriptor.page.getViewport({ rotation, scale: zoom }),
    [descriptor.page, rotation, zoom]
  )

  React.useEffect(() => {
    if (!nearViewport || !textLayerRef.current || !descriptor.TextLayer) {
      return undefined
    }
    let active = true
    let textLayer
    const renderText = async () => {
      const textContent = await descriptor.page.getTextContent()
      if (!active || !textLayerRef.current) return
      textLayerRef.current.replaceChildren()
      textLayer = new descriptor.TextLayer({
        container: textLayerRef.current,
        textContentSource: textContent,
        viewport: displayViewport,
      })
      await textLayer.render()
    }
    renderText().catch(() => undefined)
    return () => {
      active = false
      textLayer?.cancel()
    }
  }, [descriptor, displayViewport, nearViewport])

  const highlighted = activeEvidence?.pageNumber === descriptor.pageNumber
  const evidenceScale = zoom / 2

  return (
    <div
      aria-label={`Page ${descriptor.pageNumber}`}
      className={`relative shrink-0 overflow-hidden bg-white shadow-sm ring-1 ring-black/10 ${highlighted && !activeEvidence.region ? "ring-2 ring-primary" : ""}`}
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
          <div
            className="textLayer absolute inset-0 overflow-hidden text-transparent selection:bg-primary/30"
            ref={textLayerRef}
          />
          {renderError ? (
            <span className="absolute inset-0 grid place-items-center bg-white/95 text-xs text-destructive">
              <span className="text-center">
                Could not render page {descriptor.pageNumber}.
                <Button
                  className="mx-auto mt-2 flex"
                  onClick={() => setRenderAttempt((current) => current + 1)}
                  size="sm"
                  variant="outline"
                >
                  Retry
                </Button>
              </span>
            </span>
          ) : null}
        </>
      ) : null}
      {highlighted && activeEvidence.region && rotation === 0 ? (
        <span
          aria-label="Highlighted source region"
          className="pointer-events-none absolute rounded-sm border-2 border-primary bg-primary/20 shadow-sm"
          style={{
            height: activeEvidence.region.height * evidenceScale,
            left: activeEvidence.region.x * evidenceScale,
            top: activeEvidence.region.y * evidenceScale,
            width: activeEvidence.region.width * evidenceScale,
          }}
        />
      ) : null}
      <span className="absolute right-2 bottom-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white">
        {descriptor.pageNumber}
      </span>
    </div>
  )
}

function PdfThumbnail({ active, descriptor, onSelect }) {
  const canvasRef = React.useRef(null)
  React.useEffect(() => {
    if (!canvasRef.current) return undefined
    const viewport = descriptor.page.getViewport({
      scale: 72 / descriptor.width,
    })
    const canvas = canvasRef.current
    canvas.width = Math.ceil(viewport.width)
    canvas.height = Math.ceil(viewport.height)
    const task = descriptor.page.render({ canvas, viewport })
    task.promise.catch(() => undefined)
    return () => task.cancel()
  }, [descriptor])
  return (
    <button
      aria-label={`Go to page ${descriptor.pageNumber}`}
      className={`rounded border p-1 transition-colors ${active ? "border-primary bg-primary/10" : "border-transparent hover:border-border"}`}
      onClick={onSelect}
      type="button"
    >
      <canvas aria-hidden="true" className="block bg-white" ref={canvasRef} />
      <span className="mt-1 block text-center text-[10px] text-muted-foreground">
        {descriptor.pageNumber}
      </span>
    </button>
  )
}

function PdfPreview({ activeEvidence, sourceUrl }) {
  const [pages, setPages] = React.useState([])
  const [error, setError] = React.useState(null)
  const [loadAttempt, setLoadAttempt] = React.useState(0)
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
        setError(null)
        const { GlobalWorkerOptions, getDocument, TextLayer } =
          await import("pdfjs-dist")
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
              TextLayer,
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
  }, [loadAttempt, sourceUrl])

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
    for (const element of pageElements.current.values())
      observer.observe(element)
    return () => observer.disconnect()
  }, [pages, scrollRoot])

  const changeZoom = (nextZoom) => {
    setZoom(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom)))
  }

  const registerPage = React.useCallback((pageNumber, element) => {
    if (element) pageElements.current.set(pageNumber, element)
    else pageElements.current.delete(pageNumber)
  }, [])

  const goToPage = React.useCallback(
    (pageNumber, behavior = "smooth") => {
      const boundedPage = Math.min(pages.length, Math.max(1, pageNumber))
      const pageElement = pageElements.current.get(boundedPage)
      setCurrentPage(boundedPage)
      if (!scrollRoot || !pageElement) return
      const top = Math.max(0, pageElement.offsetTop - 16)
      scrollRoot.scrollTo({ behavior, top })
    },
    [pages.length, scrollRoot]
  )

  React.useEffect(() => {
    if (!activeEvidence?.pageNumber) return undefined
    const frame = window.requestAnimationFrame(() =>
      goToPage(activeEvidence.pageNumber, "smooth")
    )
    return () => window.cancelAnimationFrame(frame)
  }, [activeEvidence?.pageNumber, goToPage])

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
        <ViewerButton
          label="Zoom out"
          disabled={zoom <= MIN_ZOOM}
          onClick={() => changeZoom(zoom - ZOOM_STEP)}
          size="icon"
          variant="ghost"
        >
          <RiZoomOutLine />
        </ViewerButton>
        <span className="min-w-12 text-center text-xs text-muted-foreground">
          {Math.round(zoom * 100)}%
        </span>
        <ViewerButton
          label="Zoom in"
          disabled={zoom >= MAX_ZOOM}
          onClick={() => changeZoom(zoom + ZOOM_STEP)}
          size="icon"
          variant="ghost"
        >
          <RiZoomInLine />
        </ViewerButton>
        <ViewerButton
          label="Reset view"
          onClick={() => {
            setZoom(1)
            setRotation(0)
            goToPage(1)
          }}
          size="icon"
          variant="ghost"
        >
          <RiRestartLine />
        </ViewerButton>
        <ViewerButton
          label="Fit page"
          disabled={!pages.length}
          onClick={fitCurrentPage}
          size="icon"
          variant="ghost"
        >
          <RiAspectRatioLine />
        </ViewerButton>
        <ViewerButton
          label="Rotate clockwise"
          disabled={!pages.length}
          onClick={() => setRotation((current) => (current + 90) % 360)}
          size="icon"
          variant="ghost"
        >
          <RiClockwise2Line />
        </ViewerButton>
        <div className="ml-1 flex items-center gap-0.5 border-l pl-2">
          <ViewerButton
            label="Previous page"
            disabled={!pages.length || currentPage <= 1}
            onClick={() => goToPage(currentPage - 1)}
            size="icon"
            variant="ghost"
          >
            <RiArrowUpSLine />
          </ViewerButton>
          <span className="min-w-12 text-center text-xs text-muted-foreground">
            {pages.length ? `${currentPage} / ${pages.length}` : "…"}
          </span>
          <ViewerButton
            label="Next page"
            disabled={!pages.length || currentPage >= pages.length}
            onClick={() => goToPage(currentPage + 1)}
            size="icon"
            variant="ghost"
          >
            <RiArrowDownSLine />
          </ViewerButton>
        </div>
        <span className="ml-auto hidden items-center gap-1 text-xs text-muted-foreground sm:flex">
          <RiDragMove2Line /> Drag to pan
        </span>
      </div>
      {error ? (
        <PreviewError
          message="Could not render this PDF."
          onRetry={() => setLoadAttempt((current) => current + 1)}
        />
      ) : (
        <div className="flex min-h-0 flex-1">
          {pages.length > 1 ? (
            <aside
              aria-label="PDF page thumbnails"
              className="w-24 shrink-0 space-y-2 overflow-y-auto border-r bg-muted/30 p-2"
            >
              {pages.map((descriptor) => (
                <PdfThumbnail
                  active={currentPage === descriptor.pageNumber}
                  descriptor={descriptor}
                  key={descriptor.pageNumber}
                  onSelect={() => goToPage(descriptor.pageNumber)}
                />
              ))}
            </aside>
          ) : null}
          <div
            aria-label="PDF document viewer"
            className="min-h-96 flex-1 cursor-grab overflow-auto bg-muted/50 p-4 active:cursor-grabbing"
            onPointerCancel={stopDragging}
            onPointerDown={(event) => {
              if (event.button !== 0) return
              if (event.target.closest?.(".textLayer")) return
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
                  activeEvidence={activeEvidence}
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
        </div>
      )}
    </div>
  )
}

export function SourcePreview({
  activeEvidence,
  error,
  loading,
  mimeType,
  onRetry,
  sourceUrl,
}) {
  return (
    <TooltipProvider>
      <section className="sticky top-5 flex h-[calc(100svh-7rem)] min-h-[32rem] flex-col overflow-hidden rounded-2xl border bg-card shadow-sm">
        <div className="border-b px-4 py-3">
          <h2 className="font-semibold">Original document</h2>
        </div>
        {loading ? (
          <div className="grid flex-1 place-items-center text-sm text-muted-foreground">
            Loading original…
          </div>
        ) : error ? (
          <PreviewError message={error.message} onRetry={onRetry} />
        ) : sourceUrl && mimeType === "application/pdf" ? (
          <PdfPreview activeEvidence={activeEvidence} sourceUrl={sourceUrl} />
        ) : sourceUrl ? (
          <ImagePreview activeEvidence={activeEvidence} sourceUrl={sourceUrl} />
        ) : null}
      </section>
    </TooltipProvider>
  )
}
