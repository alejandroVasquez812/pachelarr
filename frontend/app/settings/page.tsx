import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { Text } from "@/components/Text";
import { fetchSettings, fetchOverrides, ApiError } from "@/lib/api";
import SettingsClient from "@/components/SettingsClient";
import ParamOverridesClient from "@/components/ParamOverridesClient";
import AdminActionsClient from "@/components/AdminActionsClient";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  let settings;
  try {
    settings = await fetchSettings();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      return (
        <Card>
          <Title>Settings unavailable</Title>
          <Text>
            Pachelarr API key not configured on the frontend — set
            PACHELARR_API_KEY in the frontend environment to manage settings.
          </Text>
        </Card>
      );
    }
    throw e;
  }

  // Fetch param overrides (best-effort — may 401 if key not configured).
  let overrides = {};
  try {
    overrides = await fetchOverrides();
  } catch {
    // If the key is not configured, the settings fetch above would have
    // already returned the 401 card. If we reach here, it's a transient error;
    // fall back to empty overrides so the page still renders.
  }

  return (
    <div className="space-y-6">
      <SettingsClient initial={settings} />
      <ParamOverridesClient initial={overrides} />
      <AdminActionsClient />
    </div>
  );
}