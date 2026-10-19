import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { DEFAULT_API_URL } from "./client.js";
import { loadCredentials, saveCredentials } from "./credentials.js";
import { createRendaServer } from "./server.js";

/** Run the local (stdio) Renda MCP server with the shared credentials file. */
export async function serveStdio(): Promise<void> {
  const creds = await loadCredentials();
  const baseUrl = process.env.RENDA_API_URL || creds.apiUrl || DEFAULT_API_URL;
  const server = createRendaServer({
    mode: "local",
    baseUrl,
    apiKey: process.env.RENDA_API_KEY || creds.apiKey,
    claimUrl: creds.claimUrl,
    onApiKey: (key) => saveCredentials({ apiKey: key, apiUrl: baseUrl }),
    onTrial: (t) => saveCredentials({ apiKey: t.apiKey, apiUrl: baseUrl, claimUrl: t.claimUrl, trialExpiresAt: t.expiresAt }),
  });
  await server.connect(new StdioServerTransport());
}
