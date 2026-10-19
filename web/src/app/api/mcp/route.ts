import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { RendaClient, registerRendaTools } from "@rendahq/mcp/remote";
import { authenticateKey, bearer } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { appUrl } from "@/lib/env";
import { clientIp, FORWARDED_IP_HEADER, signForwardedIp } from "@/lib/clientip";

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
  // The tools call this API from our own server, so per-IP limits (signup, trials) would otherwise
  // see one shared server address. Forward the real caller's IP, signed so clients can't forge it.
  const forwarded = signForwardedIp(clientIp(req));
  const base = appUrl();
  const fetchWithIp: typeof fetch = (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!url.startsWith(base)) return fetch(input, init);
    const headers = new Headers(init?.headers);
    headers.set(FORWARDED_IP_HEADER, forwarded);
    return fetch(input, { ...init, headers });
  };
  registerRendaTools(server, { client: new RendaClient({ baseUrl: base, apiKey: key ?? undefined, fetch: fetchWithIp }), mode: "remote" });
  const transport = new WebStandardStreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
  await server.connect(transport);
  return transport.handleRequest(req);
}

export { handle as GET, handle as POST, handle as DELETE };
