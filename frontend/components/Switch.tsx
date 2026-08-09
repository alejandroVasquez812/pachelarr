// Tremor Switch [v1.0.0]
import React from "react"
import * as SwitchPrimitives from "@radix-ui/react-switch"
import { tv, type VariantProps } from "tailwind-variants"

import { cx, focusRing } from "@/lib/utils"

const switchVariants = tv({
  slots: {
    root: [
      // base
      "group relative isolate inline-flex shrink-0 cursor-pointer items-center rounded-full p-0.5 shadow-inner outline-hidden ring-1 ring-inset transition-all",
      "bg-[var(--border-strong)]",
      // ring color
      "ring-[var(--border)]",
      // checked
      "data-[state=checked]:bg-[var(--accent)]",
      // disabled
      "data-disabled:cursor-default",
      // disabled checked
      "data-disabled:data-[state=checked]:bg-[var(--accent)]/30",
      "data-disabled:data-[state=checked]:ring-[var(--border-strong)]",
      // disabled checked dark
      "dark:data-disabled:data-[state=checked]:ring-gray-900",
      // disabled unchecked
      "data-disabled:data-[state=unchecked]:ring-[var(--border-strong)]",
      "data-disabled:data-[state=unchecked]:bg-[var(--bg)]",
      // disabled unchecked dark
      "dark:data-disabled:data-[state=unchecked]:bg-[var(--surface)]",
      focusRing,
    ],
    thumb: [
      // base
      "pointer-events-none relative inline-block transform appearance-none rounded-full border-none shadow-lg outline-hidden transition-all duration-150 ease-in-out focus:border-none focus:outline-hidden focus:outline-transparent",
      // background color
      "bg-[var(--surface)]",
      // disabled
      "group-data-disabled:shadow-none",
      "group-data-disabled:bg-[var(--muted)]",
    ],
  },
  variants: {
    size: {
      default: {
        root: "h-5 w-9",
        thumb:
          "h-4 w-4 data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0",
      },
      small: {
        root: "h-4 w-7",
        thumb:
          "h-3 w-3 data-[state=checked]:translate-x-3 data-[state=unchecked]:translate-x-0",
      },
    },
  },
  defaultVariants: {
    size: "default",
  },
})

interface SwitchProps
  extends Omit<
      React.ComponentPropsWithoutRef<typeof SwitchPrimitives.Root>,
      "asChild"
    >,
    VariantProps<typeof switchVariants> {}

const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitives.Root>,
  SwitchProps
>(({ className, size, ...props }: SwitchProps, forwardedRef) => {
  const { root, thumb } = switchVariants({ size })
  return (
    <SwitchPrimitives.Root
      ref={forwardedRef}
      className={cx(root(), className)}
      tremor-id="tremor-raw"
      {...props}
    >
      <SwitchPrimitives.Thumb className={cx(thumb())} />
    </SwitchPrimitives.Root>
  )
})
Switch.displayName = "Switch"

export { Switch }
