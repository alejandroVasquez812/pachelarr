import { Card, Callout } from "@tremor/react";

export default function IndexerCacheFreshness({
  ageSeconds,
}: {
  ageSeconds: number | null;
}) {
  if (ageSeconds === null) {
    return (
      <Card>
        <Callout title="No indexer listing cached yet" color="neutral">
          A search will fetch and cache the Prowlarr indexer listing.
        </Callout>
      </Card>
    );
  }

  if (ageSeconds < 0) {
    return (
      <Card>
        <Callout title="STALE — indexer listing expired" color="rose">
          The cached listing is past its TTL. The next search will refresh it.
        </Callout>
      </Card>
    );
  }

  return (
    <Card>
      <Callout title={`Fresh — expires in ${ageSeconds}s`} color="emerald">
        The indexer listing is still within its cache TTL.
      </Callout>
    </Card>
  );
}
