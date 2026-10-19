import { appUrl } from "@/lib/env";
import { MCP_PACKAGE } from "@/lib/install-snippets";

/** MCP server discovery (registry server.json shape) for agents and clients. */
export function GET() {
  const base = appUrl();
  return Response.json({
    $schema: "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
    name: "io.github.vochsel/renda",
    title: "Renda",
    description: "Render 3D files and scenes (.blend, USD, glTF, three.js JSON, FBX, OBJ, STL…) and HyperFrames videos in the cloud.",
    version: "0.1.0",
    websiteUrl: base,
    remotes: [{ type: "streamable-http", url: `${base}/api/mcp`, headers: [{ name: "Authorization", description: "Bearer rnd_live_… API key (optional for renda_trial / renda_signup / renda_verify)", isSecret: true }] }],
    packages: [{ registryType: "npm", identifier: MCP_PACKAGE, version: "0.1.0", transport: { type: "stdio" }, environmentVariables: [{ name: "RENDA_API_KEY", description: "Renda API key (optional: the server can start a trial)", isSecret: true }] }],
    _meta: { docs: `${base}/llms.txt`, auth: `${base}/auth.md`, openapi: `${base}/api/v1/openapi.json` },
  });
}
