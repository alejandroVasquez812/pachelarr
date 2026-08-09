"use client";

import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { Text } from "@/components/Text";
import { Button } from "@/components/Button";
import { Input } from "@/components/Input";
import { Switch } from "@/components/Switch";
import { Divider } from "@/components/Divider";
import type { SettingEntry, SettingsGroupKey } from "@/lib/types";
import { validateField } from "@/lib/validate";
import SecretField from "./SecretField";

interface SettingsGroupCardProps {
  groupName: string;
  entries: { keyDef: SettingsGroupKey; entry: SettingEntry }[];
  dirtyKeys: Set<string>;
  errors: Record<string, string>;
  onFieldChange: (key: string, rawString: string) => void;
  onSaveGroup: () => void;
  isSaving: boolean;
}

function boolValue(entry: SettingEntry): boolean {
  const v = entry.value;
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0;
  const s = String(v ?? "").trim().toLowerCase();
  return ["1", "true", "yes"].includes(s);
}

export default function SettingsGroupCard({
  groupName,
  entries,
  dirtyKeys,
  errors,
  onFieldChange,
  onSaveGroup,
  isSaving,
}: SettingsGroupCardProps) {
  const groupDirty = entries.some(({ keyDef }) => dirtyKeys.has(keyDef.key));
  const hasClientError = entries.some(
    ({ keyDef, entry }) =>
      dirtyKeys.has(keyDef.key) && validateField(keyDef.key, String(entry.value ?? ""), entry),
  );

  return (
    <Card>
      <div className="flex items-center justify-between gap-4">
        <Title>{groupName}</Title>
        <Button
          variant="primary"
          onClick={onSaveGroup}
          disabled={!groupDirty || hasClientError || isSaving}
          isLoading={isSaving}
        >
          Save
        </Button>
      </div>
      <Divider />
      <div className="space-y-5">
        {entries.map(({ keyDef, entry }) => {
          const key = keyDef.key;
          const label = keyDef.label;
          const dirty = dirtyKeys.has(key);
          const error = errors[key];
          const raw = String(entry.value ?? "");
          return (
            <div key={key}>
              <div className="flex items-center gap-2">
                <label
                  htmlFor={`setting-${key}`}
                  className="text-sm text-[var(--muted)]"
                >
                  {label}
                </label>
                {dirty && (
                  <span
                    className="inline-flex items-center text-xs font-medium text-[var(--accent)]"
                    aria-hidden="true"
                  >
                    <span className="mr-1.5 h-1.5 w-1.5 rounded-full bg-[var(--accent)]" />
                    modified
                  </span>
                )}
              </div>
              <div className="mt-1.5">
                {entry.secret ? (
                  <SecretField
                    entry={entry}
                    value={raw}
                    onChange={(v) => onFieldChange(key, v)}
                    onGenerate={
                      key === "PACHELARR_API_KEY"
                        ? () => onFieldChange(key, crypto.randomUUID())
                        : undefined
                    }
                  />
                ) : entry.type === "bool" ? (
                  <Switch
                    id={`setting-${key}`}
                    checked={boolValue(entry)}
                    onCheckedChange={(v) => onFieldChange(key, String(v))}
                  />
                ) : (
                  <Input
                    id={`setting-${key}`}
                    type={entry.type === "int" || entry.type === "float" ? "number" : "text"}
                    value={raw}
                    onChange={(e) => onFieldChange(key, e.target.value)}
                  />
                )}
              </div>
              {error && (
                <Text className="mt-1.5 text-sm text-[var(--error)]">
                  {error}
                </Text>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}