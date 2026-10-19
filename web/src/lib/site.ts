/** Public identity of the deployment. Change here when the domain / repo / npm scope changes. */
export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://renda.vochsel.com").replace(/\/$/, "");
/** GitHub repo slug used by `npx skills add` and `/plugin marketplace add`. (Repo not created yet.) */
export const GITHUB_REPO = "vochsel/renda";
export const NPM_SCOPE = "@rendahq";
