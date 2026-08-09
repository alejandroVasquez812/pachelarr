"use client";

import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { Text } from "@/components/Text";
import { Button } from "@/components/Button";
import { Input } from "@/components/Input";
import { Switch } from "@/components/Switch";
import { Divider } from "@/components/Divider";
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
          variant="primary"
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
                <Text className="font-medium text-gray-900 dark:text-gray-50">
                  {key}
                </Text>
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
                    onCheckedChange={(v) => onFieldChange(key, String(v))}
                  />
                ) : (
                  <Input
                    type={entry.type === "int" || entry.type === "float" ? "number" : "text"}
                    value={raw}
                    onChange={(e) => onFieldChange(key, e.target.value)}
                  />
                )}
              </div>
              <div className="mt-1 flex items-center justify-between">
                <Text color="subtle">
                  default: {String(entry.default ?? "")}
                </Text>
                <Button
                  variant="light"
                  onClick={() => onResetField(key)}
                >
                  Reset
                </Button>
              </div>
              {error && (
                <Text className="mt-1 text-red-600 dark:text-red-500">
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