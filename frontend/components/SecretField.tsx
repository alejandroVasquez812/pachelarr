"use client";

import { useState } from "react";
import { Input } from "@/components/Input";
import { Button } from "@/components/Button";
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
      <Button
        variant="secondary"
        onClick={() => setRevealed((r) => !r)}
      >
        {revealed ? "Hide" : "Reveal"}
      </Button>
      {onGenerate && (
        <Button variant="light" onClick={onGenerate}>
          Generate
        </Button>
      )}
    </div>
  );
}