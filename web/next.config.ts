import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

// No nonces (that would make every page dynamic). Sources: model-viewer from unpkg (+ Draco/KTX2
// decoders from gstatic, run as wasm in blob workers), render outputs and thumbnails served from R2
// presigned URLs, and form posts / redirects to WorkOS AuthKit and Stripe Checkout/Portal.
const R2 = "https://*.r2.cloudflarestorage.com";
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' https://unpkg.com https://www.gstatic.com${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${R2}`,
  `media-src 'self' blob: ${R2}`,
  "font-src 'self' data:",
  // blob:/data: — model-viewer fetches a GLB's embedded textures through blob URLs.
  `connect-src 'self' blob: data: ${R2} https://unpkg.com https://www.gstatic.com`,
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self' https://api.workos.com https://*.authkit.app https://checkout.stripe.com https://billing.stripe.com",
  "frame-ancestors 'none'",
  ...(isDev ? [] : ["upgrade-insecure-requests"]),
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
];

const nextConfig: NextConfig = {
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

export default nextConfig;
