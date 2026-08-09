"use client";

import { useMemo, useState } from "react";
import toast from "react-hot-toast";
import { Button } from "@tremor/react";
import type {
  SettingsSnapshot,
  PutSettingsError,
} from "@/lib/types";
import { SETTINGS_GROUPS, REQUIRED_SECRETS } from "@/lib/types";
import { coerceValue } from "@/lib/validate";
import SettingsGroupCard from "./SettingsGroupCard";
import RestartRequiredBanner from "./RestartRequiredBanner";

interface SettingsClientProps {
  initial: SettingsSnapshot;
}

export default function SettingsClient({ initial }: SettingsClientProps) {
  const [snapshot, setSnapshot] = useState<SettingsSnapshot>(initial);
  const [draft, setDraft] = useState<SettingsSnapshot>(initial);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [savingGroup, setSavingGroup] = useState<string | null>(null);

  const dirtyKeys = useMemo(() => {
    const set = new Set<string>();
    for (const key of Object.keys(draft)) {
      if (draft[key].value !== snapshot[key].value) set.add(key);
    }
    return set;
  }, [draft, snapshot]);

  const handleFieldChange = (key: string, raw: string) => {
    setDraft((prev) => ({
      ...prev,
      [key]: { ...prev[key], value: raw },
    }));
    setErrors((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const handleResetField = (key: string) => {
    setDraft((prev) => ({
      ...prev,
      [key]: { ...prev[key], value: prev[key].default },
    }));
    setErrors((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const handleDiscardAll = () => {
    setDraft(snapshot);
    setErrors({});
  };

  const handleSaveGroup = async (groupName: string) => {
    const group = SETTINGS_GROUPS.find((g) => g.name === groupName);
    if (!group) return;

    const dirtyInGroup = group.keys.filter((key) => dirtyKeys.has(key));
    if (dirtyInGroup.length === 0) return;

    const body: Record<string, string | number | boolean | null> = {};
    for (const key of dirtyInGroup) {
      const raw = String(draft[key].value ?? "");
      // Blank-secret guard: confirm before clearing a required secret.
      if (raw === "" && REQUIRED_SECRETS.has(key)) {
        if (!window.confirm(`Clear ${key}? This cannot be undone.`)) {
          continue;
        }
      }
      body[key] = coerceValue(raw, draft[key]);
    }

    if (Object.keys(body).length === 0) return;

    const prevDraft = draft;
    setSavingGroup(groupName);
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (res.ok) {
        const result = await res.json();
        const appliedCount = Object.keys(result.applied ?? {}).length;
        toast.success(`${appliedCount} setting(s) saved`);
        // Update the saved snapshot to the server's fresh state, but
        // preserve unsaved edits in OTHER groups by re-applying any draft
        // values that are still dirty (i.e., not part of this PUT body).
        const fresh: SettingsSnapshot = result.settings;
        setSnapshot(fresh);
        setDraft((prev) => {
          const next: SettingsSnapshot = { ...fresh };
          for (const key of Object.keys(prev)) {
            // Was this key dirty before the save (i.e., edited locally)?
            const wasDirty = prevDraft[key].value !== snapshot[key].value;
            // Was it NOT part of the body we just PUT?
            const wasPut = Object.prototype.hasOwnProperty.call(body, key);
            if (wasDirty && !wasPut) {
              // Preserve the user's unsaved local edit for this key.
              next[key] = prevDraft[key];
            }
          }
          return next;
        });
        setErrors((prev) => {
          const next = { ...prev };
          for (const key of Object.keys(body)) delete next[key];
          return next;
        });
      } else {
        const parsed = (await res.json()) as PutSettingsError;
        const failedKeys = Object.keys(parsed.errors ?? {});
        toast.error(`Some settings failed: ${failedKeys.join(", ")}`);
        setErrors(parsed.errors ?? {});
        // Re-fetch the full snapshot to stay consistent with the backend,
        // then re-apply the still-dirty local edits for the failed keys.
        const fresh = await fetch("/api/settings", { cache: "no-store" });
        if (fresh.ok) {
          const freshSnapshot = (await fresh.json()) as SettingsSnapshot;
          setSnapshot(freshSnapshot);
          setDraft((prev) => {
            const next = { ...freshSnapshot };
            for (const key of failedKeys) {
              if (prevDraft[key]) next[key] = prevDraft[key];
            }
            return next;
          });
        }
      }
    } catch (e) {
      toast.error("Failed to save settings");
    } finally {
      setSavingGroup(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
          Settings
        </h1>
        <Button
          variant="secondary"
          onClick={handleDiscardAll}
          disabled={dirtyKeys.size === 0}
        >
          Discard all changes
        </Button>
      </div>

      <RestartRequiredBanner snapshot={snapshot} draft={draft} />

      {SETTINGS_GROUPS.map((group) => (
        <SettingsGroupCard
          key={group.name}
          groupName={group.name}
          entries={group.keys
            .filter((key) => draft[key])
            .map((key) => ({ key, entry: draft[key] }))}
          dirtyKeys={dirtyKeys}
          errors={errors}
          onFieldChange={handleFieldChange}
          onResetField={handleResetField}
          onSaveGroup={() => handleSaveGroup(group.name)}
          isSaving={savingGroup === group.name}
        />
      ))}
    </div>
  );
}
