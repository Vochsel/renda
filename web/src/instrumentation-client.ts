import posthog from "posthog-js";

// Browser analytics + error tracking. Only when a key is configured (production); proxied through
// /ingest so it's first-party (CSP stays 'self', fewer blocked requests).
const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
if (key) {
  posthog.init(key, {
    api_host: "/ingest",
    ui_host: "https://us.posthog.com",
    defaults: "2025-05-24", // pageviews + pageleaves on client-side navigation
    person_profiles: "identified_only",
    capture_exceptions: true,
    session_recording: { maskAllInputs: true, maskTextSelector: "[data-ph-mask]" },
  });
  posthog.register({ app: "renda", environment: process.env.NEXT_PUBLIC_VERCEL_ENV ?? process.env.NODE_ENV });
}
