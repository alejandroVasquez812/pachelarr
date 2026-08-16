"use client";

import { useMemo, useState } from "react";
import { Button } from "@/components/Button";
import { useToast } from "@/lib/useToast";
import type {
  SettingsSnapshot,
  PutSettingsError,
  SettingsGroup,
} from "@/lib/types";
import { SETTINGS_GROUPS, SETTINGS_TABS, SETTINGS_TAB_ID_DEFAULT, REQUIRED_SECRETS } from "@/lib/types";
import { coerceValue } from "@/lib/validate";
import SettingsGroupCard from "./SettingsGroupCard";
import RestartRequiredBanner from "./RestartRequiredBanner";
import SettingsTabs from "./SettingsTabs";
import SettingsSearch from "./SettingsSearch";

interface SettingsClientProps {
  initial: SettingsSnapshot;
}

export default function SettingsClient({ initial }: SettingsClientProps) {
  const { toast } = useToast();
  const [snapshot, setSnapshot] = useState<SettingsSnapshot>(initial);
  const [draft, setDraft] = useState<SettingsSnapshot>(initial);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [savingGroup, setSavingGroup] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<string>(SETTINGS_TAB_ID_DEFAULT);
  const [query, setQuery] = useState<string>("");

  const normalizedQuery = query.trim().toLowerCase();

  const filteredGroups: SettingsGroup[] = useMemo(() => {
    return SETTINGS_GROUPS.map((group) => {
      const matchingKeys = group.keys.filter((k) => {
        const entry = draft[k.key];
        if (!entry) return false;
        if (!normalizedQuery) return true;
        const haystack = `${group.name.toLowerCase()} ${k.key.toLowerCase()} ${k.label.toLowerCase()} ${String(entry.value ?? "").toLowerCase()}`;
        return haystack.includes(normalizedQuery);
      });
      return { ...group, keys: matchingKeys };
    }).filter((group) => group.keys.length > 0);
  }, [draft, normalizedQuery]);

  const visibleGroups: SettingsGroup[] = useMemo(() => {
    if (normalizedQuery) return filteredGroups;
    return filteredGroups.filter((group) => group.tab === activeTab);
  }, [filteredGroups, activeTab, normalizedQuery]);

  const resultCount = useMemo(
    () => filteredGroups.reduce((sum, g) => sum + g.keys.length, 0),
    [filteredGroups]
  );

  const activeTabHasGroups = useMemo(
    () => filteredGroups.some((g) => g.tab === activeTab),
    [filteredGroups, activeTab]
  );

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

  const handleDiscardAll = () => {
    setDraft(snapshot);
    setErrors({});
  };

  const handleSaveGroup = async (groupName: string) => {
    const group = SETTINGS_GROUPS.find((g) => g.name === groupName);
    if (!group) return;

    const dirtyInGroup = group.keys
      .map((k) => k.key)
      .filter((key) => dirtyKeys.has(key));
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
        toast({
          title: "Settings saved",
          description: `${appliedCount} setting(s) updated.`,
          variant: "success",
        });
        // Update the saved snapshot to the server's fresh state, but
        // preserve unsaved edits in OTHER groups by re-applying any draft
        // values that are still dirty (i.e., not part of this PUT body).
        const fresh: SettingsSnapshot = result.settings;
        setSnapshot(fresh);
        setDraft((prev) => {
          const next: SettingsSnapshot = { ...fresh };
          for (const key of Object.keys(prev)) {
            const wasDirty = prevDraft[key].value !== snapshot[key].value;
            const wasPut = Object.prototype.hasOwnProperty.call(body, key);
            if (wasDirty && !wasPut) {
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
        toast({
          title: "Some settings failed",
          description: failedKeys.join(", "),
          variant: "error",
        });
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
      toast({
        title: "Error",
        description: "Failed to save settings.",
        variant: "error",
      });
    } finally {
      setSavingGroup(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-50">
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

      <SettingsSearch value={query} onChange={setQuery} resultCount={resultCount} />

      <SettingsTabs
        tabs={SETTINGS_TABS}
        activeTab={normalizedQuery ? "" : activeTab}
        onChange={(tabId) => {
          setActiveTab(tabId);
          setQuery("");
          if (typeof window !== "undefined") {
            window.location.hash = `tab-${tabId}`;
          }
        }}
      />

      {visibleGroups.length === 0 && (
        <p className="text-sm text-[var(--muted)]">
          {normalizedQuery
            ? "No settings match your search."
            : activeTabHasGroups
              ? "All settings in this tab are hidden by the current search."
              : "No settings are available for this section."}
        </p>
      )}

      {visibleGroups.map((group) => (
        <div key={group.name} id={`group-${group.name}`}>
          <SettingsGroupCard
            groupName={group.name}
            entries={group.keys
              .filter((k) => draft[k.key])
              .map((k) => ({ keyDef: k, entry: draft[k.key] }))}
            dirtyKeys={dirtyKeys}
            errors={errors}
            onFieldChange={handleFieldChange}
            onSaveGroup={() => handleSaveGroup(group.name)}
            isSaving={savingGroup === group.name}
          />
        </div>
      ))}
    </div>
  );
}