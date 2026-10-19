"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Re-render the server component every `ms` while `active` (live job progress without a client data layer). */
export function AutoRefresh({ active, ms = 2000 }: { active: boolean; ms?: number }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => router.refresh(), ms);
    return () => clearInterval(t);
  }, [active, ms, router]);
  return null;
}
