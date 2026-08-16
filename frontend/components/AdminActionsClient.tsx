"use client";

import { useState } from "react";
import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { Text } from "@/components/Text";
import { Button } from "@/components/Button";
import { Divider } from "@/components/Divider";
import { useToast } from "@/lib/useToast";

export default function AdminActionsClient() {
  const { toast } = useToast();
  const [flushing, setFlushing] = useState(false);
  const [resetting, setResetting] = useState<string | null>(null);

  const handleFlushIndexers = async () => {
    if (!window.confirm("Flush the indexer listing cache? The next search will re-fetch from Prowlarr.")) return;
    setFlushing(true);
    try {
      const res = await fetch("/api/admin/cache/indexers/invalidate", { method: "POST" });
      if (res.ok) {
        toast({ title: "Cache flushed", description: "Indexer listing cache invalidated.", variant: "success" });
      } else {
        toast({ title: "Failed", description: "Could not flush cache.", variant: "error" });
      }
    } catch {
      toast({ title: "Error", description: "Request failed.", variant: "error" });
    } finally {
      setFlushing(false);
    }
  };

  const handleReset = async (endpoint: string, label: string, confirmMsg: string) => {
    if (!window.confirm(confirmMsg)) return;
    setResetting(endpoint);
    try {
      const res = await fetch(`/api/admin/${endpoint}`, { method: "POST" });
      if (res.ok) {
        toast({ title: `${label} reset`, description: "Stats cleared successfully.", variant: "success" });
      } else {
        toast({ title: "Failed", description: `Could not reset ${label}.`, variant: "error" });
      }
    } catch {
      toast({ title: "Error", description: "Request failed.", variant: "error" });
    } finally {
      setResetting(null);
    }
  };

  return (
    <Card>
      <div>
        <Title>Cache Management</Title>
        <Text color="subtle" className="mt-1">
          Invalidate in-memory and persisted caches.
        </Text>
      </div>
      <Divider />
      <div className="flex items-center gap-3">
        <Button variant="secondary" onClick={handleFlushIndexers} disabled={flushing} isLoading={flushing}>
          Flush indexer listing cache
        </Button>
        <Text color="subtle" className="text-sm">
          Clears the cached Prowlarr indexer list. The next search will re-fetch it.
        </Text>
      </div>

      <Divider />

      <div>
        <Title>Reset Statistics</Title>
        <Text color="subtle" className="mt-1">
          Reset collected stats counters. These actions cannot be undone.
        </Text>
      </div>
      <Divider />
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="destructive"
          onClick={() => handleReset("statsz/reset", "All stats", "Reset ALL stats (per-indexer + search history)? This cannot be undone.")}
          disabled={!!resetting}
          isLoading={resetting === "statsz/reset"}
        >
          Reset all stats
        </Button>
        <Button
          variant="destructive"
          onClick={() => handleReset("statsz/reset/indexers", "Per-indexer stats", "Reset all per-indexer stats? This cannot be undone.")}
          disabled={!!resetting}
          isLoading={resetting === "statsz/reset/indexers"}
        >
          Reset per-indexer stats
        </Button>
        <Button
          variant="destructive"
          onClick={() => handleReset("statsz/reset/searches", "Search history", "Clear all search history? This cannot be undone.")}
          disabled={!!resetting}
          isLoading={resetting === "statsz/reset/searches"}
        >
          Reset search history
        </Button>
      </div>
    </Card>
  );
}