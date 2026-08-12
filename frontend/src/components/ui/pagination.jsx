import {
  RiArrowLeftSLine,
  RiArrowRightSLine,
  RiMoreLine,
} from "@remixicon/react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

function Pagination({ className, ...props }) {
  return (
    <nav
      aria-label="pagination"
      className={cn("mx-auto flex w-full justify-center", className)}
      data-slot="pagination"
      role="navigation"
      {...props}
    />
  )
}

function PaginationContent({ className, ...props }) {
  return (
    <ul
      className={cn("flex items-center gap-0.5", className)}
      data-slot="pagination-content"
      {...props}
    />
  )
}

function PaginationItem(props) {
  return <li data-slot="pagination-item" {...props} />
}

function PaginationLink({ className, isActive, size = "icon", ...props }) {
  return (
    <Button
      className={cn(className)}
      nativeButton={false}
      render={
        <a
          aria-current={isActive ? "page" : undefined}
          data-active={isActive}
          data-slot="pagination-link"
          {...props}
        />
      }
      size={size}
      variant={isActive ? "outline" : "ghost"}
    />
  )
}

function PaginationPrevious({ className, text = "Previous", ...props }) {
  return (
    <PaginationLink
      aria-label="Go to previous page"
      className={cn("pl-2!", className)}
      size="default"
      {...props}
    >
      <RiArrowLeftSLine data-icon="inline-start" />
      <span className="hidden sm:block">{text}</span>
    </PaginationLink>
  )
}

function PaginationNext({ className, text = "Next", ...props }) {
  return (
    <PaginationLink
      aria-label="Go to next page"
      className={cn("pr-2!", className)}
      size="default"
      {...props}
    >
      <span className="hidden sm:block">{text}</span>
      <RiArrowRightSLine data-icon="inline-end" />
    </PaginationLink>
  )
}

function PaginationEllipsis({ className, ...props }) {
  return (
    <span
      aria-hidden
      className={cn(
        "flex size-7 items-center justify-center [&_svg:not([class*='size-'])]:size-3.5",
        className
      )}
      data-slot="pagination-ellipsis"
      {...props}
    >
      <RiMoreLine />
      <span className="sr-only">More pages</span>
    </span>
  )
}

export {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
}
