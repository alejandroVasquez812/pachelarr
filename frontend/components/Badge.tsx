// Tremor Badge [v1.0.0]
import React from "react"
import { tv, type VariantProps } from "tailwind-variants"

import { cx } from "@/lib/utils"

const badgeVariants = tv({
  base: cx(
    "inline-flex items-center gap-x-1 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset",
  ),
  variants: {
    variant: {
      default: [
        "bg-[var(--bg)] text-[var(--text)] ring-[var(--border-strong)]",
      ],
      neutral: [
        "bg-[var(--surface)] text-[var(--text)] ring-[var(--border)]",
      ],
      success: [
        "bg-[var(--success)]/10 text-[var(--success)] ring-[var(--success)]/30",
      ],
      error: [
        "bg-[var(--error)]/10 text-[var(--error)] ring-[var(--error)]/20",
      ],
      warning: [
        "bg-[var(--warning)]/10 text-[var(--warning)] ring-[var(--warning)]/30",
      ],
    },
  },
  defaultVariants: {
    variant: "default",
  },
})

interface BadgeProps
  extends React.ComponentPropsWithoutRef<"span">,
    VariantProps<typeof badgeVariants> {}

const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant, ...props }: BadgeProps, forwardedRef) => {
    return (
      <span
        ref={forwardedRef}
        className={cx(badgeVariants({ variant }), className)}
        tremor-id="tremor-raw"
        {...props}
      />
    )
  },
)

Badge.displayName = "Badge"

export { Badge, badgeVariants, type BadgeProps }
