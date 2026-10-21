import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { RendaClient, registerRendaTools } from "@rendahq/mcp/remote";
import { authenticateKey, bearer } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { appUrl } from "@/lib/env";
import { clientIp, FORWARDED_IP_HEADER, signForwardedIp } from "@/lib/clientip";
import { track } from "@/lib/posthog";

export const maxDuration = 800;

/**
 * Remote MCP (streamable HTTP, stateless). Bearer API key is optional so agents can
 * call renda_signup / renda_verify before they have one; every other tool needs it.
 */
async function handle(req: Request): Promise<Response> {
  const key = bearer(req);
  let actor: { userId?: string | null; orgId?: string | null } = {};
  if (key) {
    try {
      const a = await authenticateKey(key);
      actor = { userId: a.user?.id, orgId: a.org.id };
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
    headers.set("user-agent", "renda-mcp/0.1.0 (remote)");
    return fetch(input, { ...init, headers });
  };
  registerRendaTools(server, { client: new RendaClient({ baseUrl: base, apiKey: key ?? undefined, fetch: fetchWithIp }), mode: "remote" });
  const transport = new WebStandardStreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
  await server.connect(transport);
  if (req.method === "POST") await trackToolCalls(req.clone(), actor);
  return transport.handleRequest(req);
}

/** Which tools agents actually use (names only, never arguments). */
async function trackToolCalls(req: Request, actor: { userId?: string | null; orgId?: string | null }) {
  try {
    const body = await req.json();
    for (const m of Array.isArray(body) ? body : [body]) {
      if (m?.method === "tools/call" && typeof m.params?.name === "string") {
        track("mcp_tool_called", actor, { tool: m.params.name, transport: "remote", authenticated: Boolean(actor.orgId) });
      }
    }
  } catch {
    // not JSON / not ours to judge: the transport reports protocol errors itself
  }
}

export { handle as GET, handle as POST, handle as DELETE };
