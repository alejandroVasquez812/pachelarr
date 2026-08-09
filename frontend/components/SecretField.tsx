"use client";

import { useState } from "react";
import { TextInput, Button } from "@tremor/react";
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

  return (
    <div className="flex items-center gap-2">
      <TextInput
        type={revealed ? "text" : "password"}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        placeholder={entry.secret ? "••••••••" : ""}
        className="flex-1"
      />
      <Button
        variant="secondary"
        size="xs"
        onClick={() => setRevealed((r) => !r)}
      >
        {revealed ? "Hide" : "Reveal"}
      </Button>
      {onGenerate && (
        <Button variant="light" size="xs" onClick={onGenerate}>
          Generate
        </Button>
      )}
    </div>
  );
}
