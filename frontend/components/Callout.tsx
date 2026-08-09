// Tremor Callout [v0.0.1]
import React from "react"
import { tv, type VariantProps } from "tailwind-variants"

import { cx } from "@/lib/utils"

const calloutVariants = tv({
  base: "flex flex-col overflow-hidden rounded-md p-4 text-sm",
  variants: {
    variant: {
      default: [
        // text color
        "text-[var(--text)]",
        // background color
        "bg-[var(--surface)]",
      ],
      success: [
        // text color
        "text-[var(--success)]",
        // background color
        "bg-[var(--success)]/10",
      ],
      error: [
        // text color
        "text-[var(--error)]",
        // background color
        "bg-[var(--error)]/10",
      ],
      warning: [
        // text color
        "text-[var(--warning)]",
        // background color
        "bg-[var(--warning)]/10",
      ],
      neutral: [
        // text color
        "text-[var(--text)]",
        // background color
        "bg-[var(--bg)]",
      ],
    },
  },
  defaultVariants: {
    variant: "default",
  },
})

interface CalloutProps
  extends React.ComponentPropsWithoutRef<"div">,
    VariantProps<typeof calloutVariants> {
  title: string
  icon?: React.ElementType | React.ReactElement
}

const Callout = React.forwardRef<HTMLDivElement, CalloutProps>(
  (
    { title, icon: Icon, className, variant, children, ...props }: CalloutProps,
    forwardedRef,
  ) => {
    return (
      <div
        ref={forwardedRef}
        className={cx(calloutVariants({ variant }), className)}
        tremor-id="tremor-raw"
        {...props}
      >
        <div className={cx("flex items-start")}>
          {Icon && typeof Icon === "function" ? (
            <Icon
              className={cx("mr-1.5 h-5 w-5 shrink-0")}
              aria-hidden="true"
            />
          ) : (
            Icon
          )}
          <span className={cx("font-semibold")}>{title}</span>
        </div>
        <div className={cx("overflow-y-auto", children ? "mt-2" : "")}>
          {children}
        </div>
      </div>
    )
  },
)

Callout.displayName = "Callout"

export { Callout, calloutVariants, type CalloutProps }
