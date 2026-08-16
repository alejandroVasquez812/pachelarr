"use client";

import { Input } from "@/components/Input";

interface SettingsSearchProps {
  value: string;
  onChange: (value: string) => void;
  resultCount: number;
}

export default function SettingsSearch({ value, onChange, resultCount }: SettingsSearchProps) {
  return (
    <div className="w-full max-w-md">
      <Input
        type="search"
        placeholder="Search settings by name or value..."
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Search settings"
      />
      {value.trim() && (
        <p className="mt-1.5 text-xs text-[var(--muted)]">
          {resultCount} result{resultCount === 1 ? "" : "s"} found
        </p>
      )}
    </div>
  );
}
