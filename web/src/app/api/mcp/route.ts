import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { RendaClient, registerRendaTools } from "@rendahq/mcp/remote";
import { authenticateKey, bearer } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { appUrl } from "@/lib/env";

export const maxDuration = 800;

/**
 * Remote MCP (streamable HTTP, stateless). Bearer API key is optional so agents can
 * call renda_signup / renda_verify before they have one; every other tool needs it.
 */
async function handle(req: Request): Promise<Response> {
  const key = bearer(req);
  if (key) {
    try {
      await authenticateKey(key);
    } catch (e) {
      if (e instanceof ApiError) {
        return Response.json(e.toBody(), {
          status: e.status,
          headers: { "www-authenticate": 'Bearer realm="renda", error="invalid_token"' },
        });
      }
      throw e;
    }
  }

  const server = new McpServer(
    { name: "renda", version: "0.1.0" },
    { instructions: "Render 3D files (blend, usd, gltf/glb, fbx, obj, stl, ply, three.js JSON…) and HyperFrames videos in the cloud. Start with renda_whoami; if unauthenticated use renda_signup then renda_verify." },
  );
  registerRendaTools(server, { client: new RendaClient({ baseUrl: appUrl(), apiKey: key ?? undefined }), mode: "remote" });
  const transport = new WebStandardStreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
  await server.connect(transport);
  return transport.handleRequest(req);
}

export { handle as GET, handle as POST, handle as DELETE };
