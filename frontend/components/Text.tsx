// Tremor Raw Text (minimal, no dedicated docs page)
import React from "react"

import { cx } from "@/lib/utils"

interface TextProps extends React.ComponentPropsWithoutRef<"p"> {
  color?: "default" | "subtle"
}

const Text = React.forwardRef<HTMLParagraphElement, TextProps>(
  ({ className, color = "default", ...props }: TextProps, forwardedRef) => {
    return (
      <p
        ref={forwardedRef}
        className={cx(
          // base
          "text-sm",
          // text color
          color === "subtle"
            ? "text-gray-500 dark:text-gray-500"
            : "text-gray-600 dark:text-gray-400",
          className,
        )}
        tremor-id="tremor-raw"
        {...props}
      />
    )
  },
)

Text.displayName = "Text"

export { Text, type TextProps }
