import { appUrl } from "@/lib/env";

/** Discovery note for MCP-aware agents. */
export function GET() {
  const base = appUrl();
  return Response.json({
    name: "renda",
    description: "Render 3D files and scenes (and HyperFrames videos) in the cloud.",
    transports: [
      { type: "streamable-http", url: `${base}/api/mcp`, auth: { type: "bearer", description: "Renda API key (rnd_live_…). Optional for renda_signup/renda_verify." } },
      { type: "stdio", command: "npx", args: ["-y", "@rendahq/mcp"] },
    ],
    docs: `${base}/llms.txt`,
    auth: `${base}/auth.md`,
    server: `${base}/.well-known/mcp/server.json`,
    openapi: `${base}/api/v1/openapi.json`,
  });
}
