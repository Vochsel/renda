/**
 * Install / onboarding snippets shared by the landing page, /docs and /llms.txt.
 * Keep commands here so every surface stays in sync.
 */
import { SITE_URL, GITHUB_REPO, NPM_SCOPE } from "./site";

export const CLI_PACKAGE = `${NPM_SCOPE}/cli`;
export const MCP_PACKAGE = `${NPM_SCOPE}/mcp`;

export const installSnippets = {
  /** Node path: no install needed. */
  npx: [
    `npx ${CLI_PACKAGE} init            # trial or sign in, MCP + skills for your agent`,
    `npx ${CLI_PACKAGE} render chair.blend --res 1920x1080`,
  ],
  /** Python path: same commands, plus local rendering. */
  uvx: [
    `uvx renda trial                    # 5 free render-minutes, no signup`,
    `uvx renda render chair.blend --cloud --res 1920x1080`,
  ],
  /** Paste this into any agent. */
  agentPrompt: `set up renda — ${SITE_URL}/llms.txt`,
  mcp: {
    claudeCode: `claude mcp add renda -- npx -y ${MCP_PACKAGE}`,
    remoteUrl: `${SITE_URL}/api/mcp`,
    json: {
      mcpServers: { renda: { command: "npx", args: ["-y", MCP_PACKAGE] } },
    },
  },
  skills: `npx skills add ${GITHUB_REPO}`,
  plugin: [`/plugin marketplace add ${GITHUB_REPO}`, `/plugin install renda@renda`],
} as const;

export type InstallSnippets = typeof installSnippets;
