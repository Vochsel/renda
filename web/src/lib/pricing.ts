export type Hardware = "cpu" | "gpu-l4" | "gpu-l40s";
export const HARDWARE: Hardware[] = ["cpu", "gpu-l4", "gpu-l40s"];

/** USD per render-minute, by hardware class (container shapes: worker/renda_worker/hardware.py). */
export const PRICE_PER_MINUTE: Record<Hardware, number> = {
  cpu: 0.015,
  "gpu-l4": 0.035,
  "gpu-l40s": 0.075,
};

export const HARDWARE_LABEL: Record<Hardware, string> = {
  cpu: "CPU (8 vCPU)",
  "gpu-l4": "NVIDIA L4",
  "gpu-l40s": "NVIDIA L40S",
};

export const FREE_MINUTES = 30;

export function costUsd(hardware: Hardware, seconds: number): number {
  return round4((seconds / 60) * PRICE_PER_MINUTE[hardware]);
}

export function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}

export function pricingTable() {
  return {
    currency: "usd",
    unit: "render-minute",
    meteredPer: "second",
    freeMinutes: FREE_MINUTES,
    cachedResults: 0,
    classes: HARDWARE.map((h) => ({ hardware: h, label: HARDWARE_LABEL[h], perMinute: PRICE_PER_MINUTE[h] })),
    notes: [
      "Billed per second of container time spent on your job (scene load + render + encode).",
      "Queue time and cold start before Renda code runs are not billed.",
      "Identical re-renders (same package, asset hashes and engine version) are served from cache for $0.",
      "Prepaid: buy credit (from $5) before rendering; usage draws it down per second and credit doesn't expire. A job that would cost more than the credit left is refused, and a running job stops when credit runs out.",
    ],
  };
}
