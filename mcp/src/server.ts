import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { RendaClient } from "./client.js";
import { registerRendaTools, type RegisterOptions } from "./tools.js";

export const SERVER_INFO = { name: "renda", version: "0.1.0" };

const INSTRUCTIONS = `Renda renders 3D files (.blend, .usd/.usdz, .glb/.gltf, three.js JSON, .fbx, .obj, .stl, .ply, .dae, .3mf, .abc, Gaussian splats .ply/.splat/.spz) and HyperFrames videos in the cloud, billed per render-second.
No account? Fastest: renda_trial (no signup, ~5 free minutes) and give the human the claim link. Or renda_signup {email} → ask the human for the 6-digit emailed code → renda_verify.
Workflow: renda_render {file} gives a cheap draft with a preview image; iterate on the package (renda://docs/package), renda_estimate before final/video renders, then render final.
If a tool returns payment_required, call renda_billing_link and give the URL to the human.`;

/** A ready McpServer with all Renda tools/resources/prompts registered. */
export function createRendaServer(opts: Omit<RegisterOptions, "client"> & { client?: RendaClient; baseUrl?: string; apiKey?: string }): McpServer {
  const server = new McpServer(SERVER_INFO, { instructions: INSTRUCTIONS });
  const client = opts.client ?? new RendaClient({ baseUrl: opts.baseUrl, apiKey: opts.apiKey });
  registerRendaTools(server, { ...opts, client });
  return server;
}
