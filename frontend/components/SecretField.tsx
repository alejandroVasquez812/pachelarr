"use client";

import { useState } from "react";
import { RiEyeFill, RiEyeOffFill, RiKeyLine } from "@remixicon/react";
import { Input } from "@/components/Input";
import { cx, focusRing } from "@/lib/utils";
import type { SettingEntry } from "@/lib/types";

interface SecretFieldProps {
  entry: SettingEntry;
  value: string;
  onChange: (raw: string) => void;
  onGenerate?: () => void;
}

export default function SecretField({
  entry,
  value,
  onChange,
  onGenerate,
}: SecretFieldProps) {
  const [revealed, setRevealed] = useState(false);

  // The Tremor Raw Input toggles its own password visibility when type="password",
  // but we keep explicit control here so the reveal state is owned by this field
  // and works alongside the Generate button.
  return (
    <div className="flex items-center gap-2">
      <Input
        type={revealed ? "text" : "password"}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        placeholder={entry.secret ? "••••••••" : ""}
        className="flex-1"
      />
      <button
        type="button"
        aria-label={revealed ? "Hide secret" : "Reveal secret"}
        onClick={() => setRevealed((r) => !r)}
        className={cx(
          // base
          "inline-flex h-11 w-11 items-center justify-center rounded-md outline-hidden transition-colors",
          // text
          "text-[var(--muted)]",
          // hover
          "hover:text-[var(--text)]",
          focusRing,
        )}
      >
        {revealed ? (
          <RiEyeOffFill aria-hidden="true" className="size-5 shrink-0" />
        ) : (
          <RiEyeFill aria-hidden="true" className="size-5 shrink-0" />
        )}
      </button>
      {onGenerate && (
        <button
          type="button"
          aria-label="Generate secret"
          onClick={onGenerate}
          className={cx(
            // base
            "inline-flex h-11 w-11 items-center justify-center rounded-md outline-hidden transition-colors",
            // text
            "text-[var(--muted)]",
            // hover
            "hover:text-[var(--text)]",
            focusRing,
          )}
        >
          <RiKeyLine aria-hidden="true" className="size-5 shrink-0" />
        </button>
      )}
    </div>
  );
}