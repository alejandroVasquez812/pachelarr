"use client";

import { cx } from "@/lib/utils";
import type { SettingsTabConfig } from "@/lib/types";

interface SettingsTabsProps {
  tabs: SettingsTabConfig[];
  activeTab: string;
  onChange: (tabId: string) => void;
}

export default function SettingsTabs({ tabs, activeTab, onChange }: SettingsTabsProps) {
  return (
    <div
      role="tablist"
      aria-label="Settings sections"
      className="flex w-full gap-1 border-b border-[var(--border)] overflow-x-auto"
    >
      {tabs.map((tab) => {
        const active = tab.id === activeTab;
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(tab.id)}
            className={cx(
              "px-4 py-2 text-sm font-medium whitespace-nowrap transition-colors",
              "border-b-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring-focus)] rounded-t-md",
              active
                ? "border-[var(--accent)] text-[var(--accent)]"
                : "border-transparent text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--surface)]"
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
