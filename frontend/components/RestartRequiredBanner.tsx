"use client";

import { Callout } from "@/components/Callout";
import type { SettingsSnapshot } from "@/lib/types";

interface RestartRequiredBannerProps {
  snapshot: SettingsSnapshot;
  draft: SettingsSnapshot;
}

export default function RestartRequiredBanner({
  snapshot,
  draft,
}: RestartRequiredBannerProps) {
  const dirtyRestartKeys = Object.keys(draft).filter(
    (key) =>
      draft[key].restart_required === true &&
      draft[key].value !== snapshot[key].value,
  );

  if (dirtyRestartKeys.length === 0) return null;

  return (
    <Callout title="Restart required" variant="warning">
      <ul className="list-disc pl-5 space-y-1">
        {dirtyRestartKeys.map((key) => (
          <li key={key}>
            <span className="font-medium">{key}</span> — editing requires a
            restart to take effect; the backend will reject live edits.
          </li>
        ))}
      </ul>
    </Callout>
  );
}