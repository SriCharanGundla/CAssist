import { Checkbox as CheckboxPrimitive } from "@base-ui/react/checkbox"
import { RiCheckLine, RiSubtractLine } from "@remixicon/react"

import { cn } from "@/lib/utils"

function Checkbox({ className, indeterminate = false, ...props }) {
  return (
    <CheckboxPrimitive.Root
      className={cn(
        "peer size-4 shrink-0 rounded-[4px] border border-input bg-background text-primary-foreground shadow-xs transition-shadow outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 data-indeterminate:border-primary data-indeterminate:bg-primary data-checked:border-primary data-checked:bg-primary data-disabled:cursor-not-allowed data-disabled:opacity-50",
        className
      )}
      data-slot="checkbox"
      indeterminate={indeterminate}
      {...props}
    >
      <CheckboxPrimitive.Indicator
        className="flex size-full items-center justify-center"
        data-slot="checkbox-indicator"
      >
        {indeterminate ? (
          <RiSubtractLine className="size-3" />
        ) : (
          <RiCheckLine className="size-3" />
        )}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
}

export { Checkbox }
