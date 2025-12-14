import Constants from "expo-constants";

// Priority:
// 1) EXPO_PUBLIC_BACKEND_URL (set per environment)
// 2) manifest extra.backendUrl (optional future)
// 3) fallback to Render API

const FALLBACK = "https://animal-report-api.onrender.com";

export function getBackendBaseUrl(): string {
  const envUrl =
    process.env.EXPO_PUBLIC_BACKEND_URL ||
    (Constants.expoConfig?.extra as any)?.backendUrl ||
    FALLBACK;

  // remove trailing slash if present
  return envUrl.replace(/\/+$/, "");
}

export function getReportUrl(): string {
  return `${getBackendBaseUrl()}/report`;
}
