"use client";

import { RiSunLine, RiMoonLine } from "@remixicon/react";
import { useTheme } from "./ThemeProvider";

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === "dark";

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      className="inline-flex items-center justify-center rounded-md p-2 text-[var(--muted)] transition-colors hover:bg-[var(--surface)] hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring-focus)]"
    >
      {isDark ? (
        <RiSunLine className="size-5" aria-hidden="true" />
      ) : (
        <RiMoonLine className="size-5" aria-hidden="true" />
      )}
    </button>
  );
}

export { ThemeToggle };
