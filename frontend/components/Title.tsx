// Tremor Raw Title (minimal, no dedicated docs page)
import React from "react"

import { cx } from "@/lib/utils"

interface TitleProps extends React.ComponentPropsWithoutRef<"h2"> {}

const Title = React.forwardRef<HTMLHeadingElement, TitleProps>(
  ({ className, ...props }: TitleProps, forwardedRef) => {
    return (
      <h2
        ref={forwardedRef}
        className={cx(
          // base
          "text-lg font-semibold",
          // text color
          "text-[var(--text)]",
          className,
        )}
        tremor-id="tremor-raw"
        {...props}
      />
    )
  },
)

Title.displayName = "Title"

export { Title, type TitleProps }
