"use client";

import {
  Card,
  Title,
  Text,
  Button,
  TextInput,
  Switch,
  Divider,
} from "@tremor/react";
import type { SettingEntry } from "@/lib/types";
import { validateField } from "@/lib/validate";
import SecretField from "./SecretField";

interface SettingsGroupCardProps {
  groupName: string;
  entries: { key: string; entry: SettingEntry }[];
  dirtyKeys: Set<string>;
  errors: Record<string, string>;
  onFieldChange: (key: string, rawString: string) => void;
  onResetField: (key: string) => void;
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
  onResetField,
  onSaveGroup,
  isSaving,
}: SettingsGroupCardProps) {
  const groupDirty = entries.some(({ key }) => dirtyKeys.has(key));
  const hasClientError = entries.some(
    ({ key, entry }) =>
      dirtyKeys.has(key) && validateField(key, String(entry.value ?? ""), entry),
  );

  return (
    <Card>
      <div className="flex items-center justify-between">
        <Title>{groupName}</Title>
        <Button
          onClick={onSaveGroup}
          disabled={!groupDirty || hasClientError || isSaving}
        >
          Save
        </Button>
      </div>
      <Divider />
      <div className="space-y-4">
        {entries.map(({ key, entry }) => {
          const dirty = dirtyKeys.has(key);
          const error = errors[key];
          const raw = String(entry.value ?? "");
          return (
            <div key={key}>
              <div className="flex items-center justify-between gap-2">
                <Text className="font-medium">{key}</Text>
                {dirty && (
                  <span className="h-2 w-2 rounded-full bg-amber-500" />
                )}
              </div>
              <div className="mt-1">
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
                    checked={boolValue(entry)}
                    onChange={(v) => onFieldChange(key, String(v))}
                  />
                ) : (
                  <TextInput
                    type={entry.type === "int" || entry.type === "float" ? "number" : "text"}
                    value={raw}
                    onChange={(e) => onFieldChange(key, e.target.value)}
                  />
                )}
              </div>
              <div className="mt-1 flex items-center justify-between">
                <Text className="text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  default: {String(entry.default ?? "")}
                </Text>
                <Button
                  variant="light"
                  size="xs"
                  onClick={() => onResetField(key)}
                >
                  Reset
                </Button>
              </div>
              {error && (
                <Text color="rose" className="mt-1">
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
