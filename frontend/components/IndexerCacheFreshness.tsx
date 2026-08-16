"use client";

import { useState } from "react";
import { Card } from "@/components/Card";
import { Callout } from "@/components/Callout";
import { Button } from "@/components/Button";
import { useToast } from "@/lib/useToast";

interface Props {
  ageSeconds: number | null;
  onInvalidated?: () => void;
}

export default function IndexerCacheFreshness({ ageSeconds, onInvalidated }: Props) {
  const { toast } = useToast();
  const [flushing, setFlushing] = useState(false);

  const handleFlush = async () => {
    if (!window.confirm("Flush the indexer listing cache? The next search will re-fetch from Prowlarr.")) return;
    setFlushing(true);
    try {
      const res = await fetch("/api/admin/cache/indexers/invalidate", { method: "POST" });
      if (res.ok) {
        toast({ title: "Cache flushed", description: "Indexer listing cache invalidated.", variant: "success" });
        onInvalidated?.();
      } else {
        toast({ title: "Failed", description: "Could not flush cache.", variant: "error" });
      }
    } catch {
      toast({ title: "Error", description: "Request failed.", variant: "error" });
    } finally {
      setFlushing(false);
    }
  };

  const flushButton = (
    <Button variant="secondary" onClick={handleFlush} disabled={flushing} isLoading={flushing}>
      Flush cache
    </Button>
  );

  if (ageSeconds === null) {
    return (
      <Card>
        <div className="flex items-center justify-between gap-4">
          <Callout title="No indexer listing cached yet" variant="neutral">
            A search will fetch and cache the Prowlarr indexer listing.
          </Callout>
          {flushButton}
        </div>
      </Card>
    );
  }

  if (ageSeconds < 0) {
    return (
      <Card>
        <div className="flex items-center justify-between gap-4">
          <Callout title="STALE — indexer listing expired" variant="error">
            The cached listing is past its TTL. The next search will refresh it.
          </Callout>
          {flushButton}
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex items-center justify-between gap-4">
        <Callout title={`Fresh — expires in ${ageSeconds}s`} variant="success">
          The indexer listing is still within its cache TTL.
        </Callout>
        {flushButton}
      </div>
    </Card>
  );
}