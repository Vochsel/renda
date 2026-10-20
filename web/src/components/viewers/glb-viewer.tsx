"use client";

import Script from "next/script";
import type { HTMLAttributes } from "react";

// Same pinned CDN build the homepage bake demo uses (allowed by the CSP).
const MODEL_VIEWER = "https://unpkg.com/@google/model-viewer@4.0.0/dist/model-viewer.min.js";

declare module "react" {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace JSX {
    interface IntrinsicElements {
      "model-viewer": HTMLAttributes<HTMLElement> & Record<string, unknown>;
    }
  }
}

/** Orbitable GLB preview. Neutral environment, so baked lighting is what you see. */
export function GlbViewer({ src, alt }: { src: string; alt: string }) {
  return (
    <>
      <Script src={MODEL_VIEWER} type="module" strategy="afterInteractive" />
      <model-viewer
        src={src}
        alt={alt}
        camera-controls=""
        interaction-prompt="none"
        environment-image="neutral"
        shadow-intensity="0"
        exposure="1"
        style={{ width: "100%", height: "100%", background: "transparent", ["--poster-color" as string]: "transparent" }}
      />
    </>
  );
}
