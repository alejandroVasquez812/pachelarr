import { Card, Title, Text } from "@tremor/react";
import { fetchSettings, ApiError } from "@/lib/api";
import SettingsClient from "@/components/SettingsClient";

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

  return <SettingsClient initial={settings} />;
}
