"use client";

import posthog from "posthog-js";
import { useEffect } from "react";

/** Ties browser analytics/errors to the signed-in Renda user and org (same ids as server events). */
export function PostHogIdentify({ userId, email, orgId, orgName }: { userId: string; email: string; orgId: string; orgName: string }) {
  useEffect(() => {
    if (!process.env.NEXT_PUBLIC_POSTHOG_KEY) return;
    posthog.identify(userId, { email });
    posthog.group("organization", orgId, { name: orgName });
  }, [userId, email, orgId, orgName]);
  return null;
}
